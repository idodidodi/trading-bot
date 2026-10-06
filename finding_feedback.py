"""Versioned feedback linked to finding evidence, independent of alert delivery."""
import hashlib
import html
import json
import time
from pathlib import Path


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False)


def finding_id(source, signal):
    # Include complete evidence/rules so a changed replay cannot inherit old ratings.
    return hashlib.sha256(canonical([source, signal]).encode()).hexdigest()


def latest_report(db, root):
    exists = db.execute("SELECT 1 FROM sqlite_master WHERE name='backtest_runs'").fetchone()
    row = db.execute('SELECT report FROM backtest_runs ORDER BY id DESC LIMIT 1').fetchone() if exists else None
    saved = Path(root) / 'data/historical-2020/report.json'
    return json.loads(row[0]) if row else json.loads(saved.read_text()) if saved.exists() else None


def initialize(db):
    db.execute('''CREATE TABLE IF NOT EXISTS finding_feedback (
        finding_id TEXT PRIMARY KEY, source TEXT NOT NULL,
        evidence TEXT NOT NULL, rating INTEGER CHECK(rating BETWEEN 1 AND 5),
        comment TEXT NOT NULL, revision INTEGER NOT NULL, updated_at REAL NOT NULL)''')
    db.execute('''CREATE TABLE IF NOT EXISTS feedback_history (
        sequence INTEGER PRIMARY KEY AUTOINCREMENT, finding_id TEXT NOT NULL,
        source TEXT NOT NULL, evidence TEXT NOT NULL,
        rating INTEGER CHECK(rating BETWEEN 1 AND 5), comment TEXT NOT NULL,
        revision INTEGER NOT NULL, updated_at REAL NOT NULL,
        UNIQUE(finding_id, revision))''')


def save(store, root, body):
    source, key = body.get('source'), body.get('finding_id')
    rating, comment, revision = body.get('rating'), body.get('comment'), body.get('revision')
    if source not in ('live', 'backtest') or not isinstance(key, str) or len(key) != 64:
        raise ValueError('Invalid finding')
    if rating is not None and (type(rating) is not int or not 1 <= rating <= 5):
        raise ValueError('Rating must be between 1 and 5')
    if not isinstance(comment, str) or len(comment) > 4000:
        raise ValueError('Comment must be at most 4000 characters')
    if type(revision) is not int or revision < 0:
        raise ValueError('Invalid revision')
    with store.connect() as db:
        db.execute('BEGIN IMMEDIATE')
        if source == 'live':
            signals = (json.loads(row[0]) for row in db.execute('SELECT payload FROM signals'))
        else:
            report = latest_report(db, root)
            signals = (s for r in report['results'] for s in r['signals']) if report else []
        signal = next((s for s in signals if finding_id(source, s) == key), None)
        if signal is None:
            raise LookupError('Finding is no longer in this view. Reload before saving.')
        existing = db.execute('SELECT revision FROM finding_feedback WHERE finding_id=?', (key,)).fetchone()
        if revision != (existing[0] if existing else 0):
            raise LookupError('Feedback changed in another tab. Reload before saving.')
        value = (key, source, canonical(signal), rating, comment, revision + 1, time.time())
        db.execute('''INSERT INTO finding_feedback VALUES(?,?,?,?,?,?,?)
            ON CONFLICT(finding_id) DO UPDATE SET rating=excluded.rating,
            comment=excluded.comment, revision=excluded.revision, updated_at=excluded.updated_at''', value)
        db.execute('''INSERT INTO feedback_history
            (finding_id,source,evidence,rating,comment,revision,updated_at) VALUES(?,?,?,?,?,?,?)''', value)
        from cloud_sync import enqueue
        feedback=dict(finding_id=key,source=source,evidence=signal,rating=rating,comment=comment,revision=revision+1,updated_at=value[-1])
        enqueue(db,'feedback',key,feedback)
        enqueue(db,'feedback_history',f'{key}:{revision+1}',feedback)
    return {'saved': True, 'revision': revision + 1, 'storage': 'local', 'online': 'not configured'}


def export(store):
    with store.connect() as db:
        columns = ['sequence', 'finding_id', 'source', 'evidence', 'rating', 'comment', 'revision', 'updated_at']
        rows = db.execute('SELECT ' + ','.join(columns) + ' FROM feedback_history ORDER BY sequence')
        records = [dict(zip(columns, row)) for row in rows]
    for record in records:
        record['evidence'] = json.loads(record['evidence'])
    return {'schema_version': 1, 'feedback_history': records}


def feedback_map(db):
    return {key: (rating, comment, revision) for key, rating, comment, revision in
            db.execute('SELECT finding_id,rating,comment,revision FROM finding_feedback')}


def cell(source, signal, feedback):
    key = finding_id(source, signal)
    rating, comment, revision = feedback.get(key, (None, '', 0))
    stars = ''.join(
        f'<button type="button" class="feedback-star{ " is-filled" if rating and n <= rating else ""}" '
        f'data-rating="{n}" aria-label="{n} star{ "s" if n > 1 else ""}" '
        f'aria-pressed="{str(rating == n).lower()}" title="{n} / 5">★</button>' for n in range(1, 6))
    return (f'<td><form class="finding-feedback" data-source="{source}" data-id="{key}" data-revision="{revision}" data-rating="{rating or ""}">'
            f'<div class="feedback-stars" role="group" aria-label="Quality rating">{stars}</div>'
            f'<textarea name="comment" aria-label="Feedback comment" maxlength="4000" rows="2" placeholder="Add a comment…">{html.escape(comment)}</textarea>'
            '<span role="status" aria-live="polite"></span></form></td>')


STYLE = '''<style>
.finding-feedback{width:220px;max-width:100%}
.feedback-stars{display:flex;gap:4px;margin-bottom:8px}
.feedback-star{display:grid;place-items:center;width:34px;height:34px;padding:0;border:1px solid #374151;border-radius:7px;background:#182131;color:#64748b;font-size:23px;cursor:pointer;transition:color .12s,border-color .12s,background .12s}
.feedback-star.is-filled{color:#fbbf24;background:#302a1d;border-color:#6b5528}
.feedback-star:hover{border-color:#fbbf24;color:#fbbf24}
.feedback-star:focus-visible,.finding-feedback textarea:focus-visible{outline:2px solid #67e8f9;outline-offset:2px}
.finding-feedback textarea{display:block;width:100%;box-sizing:border-box;resize:vertical;font:13px system-ui;line-height:1.45;background:#182131;color:#e5e7eb;border:1px solid #374151;border-radius:7px;padding:8px 10px}
.finding-feedback textarea::placeholder{color:#8793a6}
.finding-feedback [role=status]{display:block;min-height:16px;font:11px system-ui;color:#94a3b8;margin-top:5px}
</style>'''
SCRIPT = '''<script>
const feedbackForms = [...document.querySelectorAll('.finding-feedback')];
const hasUnsavedFeedback = () => feedbackForms.some(form => form.dataset.dirty === 'true');
feedbackForms.forEach(form => {
  const status = form.querySelector('[role=status]');
  let timer, saving = false, edit = 0, blocked = false, retryDelay = 2000;
  const schedule = (delay = 600) => {clearTimeout(timer); timer = setTimeout(persist, delay);};
  const changed = (delay) => {
    edit++; form.dataset.dirty = 'true';
    status.textContent = blocked ? 'Changed in another tab — reload to continue.' : 'Saving…';
    if (!blocked) schedule(delay);
  };
  async function persist() {
    if (saving || blocked || form.dataset.dirty !== 'true') return;
    saving = true;
    const savedEdit = edit;
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 10000);
    try {
      const response = await fetch('/api/feedback', {method:'POST', signal:controller.signal,
        headers:{'Content-Type':'application/json'}, body:JSON.stringify({
          source:form.dataset.source, finding_id:form.dataset.id, revision:Number(form.dataset.revision),
          rating:form.dataset.rating ? Number(form.dataset.rating) : null, comment:form.elements.comment.value
        })});
      const result = await response.json();
      if (!response.ok) {
        blocked = response.status === 409 || response.status === 400 || response.status === 403;
        throw new Error(result.error || 'Save failed');
      }
      form.dataset.revision = result.revision;
      retryDelay = 2000;
      if (savedEdit === edit) {
        form.dataset.dirty = 'false'; status.textContent = 'Saved locally';
      } else schedule(0);
    } catch(error) {
      status.textContent = blocked ? error.message : 'Not saved — retrying…';
      if (!blocked) {schedule(retryDelay); retryDelay = Math.min(retryDelay * 2, 30000);}
    } finally {clearTimeout(timeout); saving = false;}
  }
  form.querySelectorAll('.feedback-star').forEach(star => {
    star.addEventListener('click', () => {
      form.dataset.rating = star.dataset.rating;
      form.querySelectorAll('.feedback-star').forEach(button => {
        button.classList.toggle('is-filled', Number(button.dataset.rating) <= Number(form.dataset.rating));
        button.setAttribute('aria-pressed', String(button === star));
      });
      changed(0);
    });
  });
  form.elements.comment.addEventListener('input', () => changed(600));
  form.elements.comment.addEventListener('blur', () => {if (!blocked) schedule(0);});
  form.addEventListener('submit', event => {event.preventDefault(); if (!blocked) schedule(0);});
});
window.addEventListener('beforeunload', event => {if(hasUnsavedFeedback()){event.preventDefault();event.returnValue='';}});
if (location.pathname === '/') setInterval(() => {
  if (!hasUnsavedFeedback() && !document.hidden && !document.activeElement.closest('.finding-feedback')) location.reload();
}, 30000);
</script>'''
