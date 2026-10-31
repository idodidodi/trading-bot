"""Reusable, opt-in Telegram dismissal with a three-second Undo grace period.

No listener or network calls run on import. The owning worker supplies its API
client, serializes access, persists state and calls expire() when due.
"""
from copy import deepcopy
from dataclasses import asdict, dataclass, field
import time
import uuid


@dataclass
class DismissibleMessage:
    chat_id: int
    message_id: int
    original: dict
    user_id: int | None = None
    key: str = field(default_factory=lambda: uuid.uuid4().hex)
    phase: str = 'active'
    delete_at: float | None = None
    extra_keyboard: list = field(default_factory=list)

    def keyboard(self, undo=False):
        action = 'undo' if undo else 'done'
        return {'inline_keyboard': [[{
            'text': '↩ Undo' if undo else '✓ Done',
            'callback_data': f'dismiss:{action}:{self.key}',
        }]] + ([] if undo else deepcopy(self.extra_keyboard))}

    def snapshot(self):
        """JSON-safe state, including original text/entities and pending deadline."""
        return deepcopy(asdict(self))

    @classmethod
    def restore(cls, state):
        return cls(**deepcopy(state))

    def handle(self, query, call, clock=time.time):
        """Handle only this message's buttons; return whether the query matched.

        call(method, body) must raise on API failure and treat Telegram's
        'message is not modified' response as success. No token is stored here.
        """
        message = query.get('message') or {}
        if (str(message.get('chat', {}).get('id')) != str(self.chat_id)
                or message.get('message_id') != self.message_id
                or (self.user_id is not None and query.get('from', {}).get('id') != self.user_id)):
            return False
        data = query.get('data')
        if data not in (f'dismiss:done:{self.key}', f'dismiss:undo:{self.key}'):
            return False
        answer = {'callback_query_id': query['id']}
        if self.phase == 'deleted':
            call('answerCallbackQuery', dict(answer, text='Already deleted'))
            return True
        # Late Undo cannot resurrect a message after the grace period.
        if self.phase == 'pending' and clock() >= self.delete_at:
            self.expire(call, clock)
            call('answerCallbackQuery', dict(answer, text='Undo window ended'))
            return True
        if data == f'dismiss:done:{self.key}' and self.phase == 'active':
            call('editMessageText', dict(chat_id=self.chat_id, message_id=self.message_id,
                text='Deleting… 3 seconds to undo', entities=[], reply_markup=self.keyboard(True)))
            self.phase = 'pending'
            self.delete_at = clock() + 3
        elif data == f'dismiss:undo:{self.key}' and self.phase == 'pending':
            call('editMessageText', dict(deepcopy(self.original), chat_id=self.chat_id,
                message_id=self.message_id, reply_markup=self.keyboard()))
            self.phase = 'active'
            self.delete_at = None
        call('answerCallbackQuery', answer)
        return True

    def expire(self, call, clock=time.time):
        """Delete when due. Failures leave the state pending for a later retry."""
        if self.phase != 'pending' or clock() < self.delete_at:
            return False
        call('deleteMessage', dict(chat_id=self.chat_id, message_id=self.message_id))
        self.phase = 'deleted'
        self.delete_at = None
        return True
