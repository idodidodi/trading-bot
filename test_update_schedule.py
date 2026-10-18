from datetime import datetime
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch
from zoneinfo import ZoneInfo

from linux_update import next_update_at
import windows_update as updates


def local_time(value):
    return datetime.fromisoformat(value).replace(tzinfo=ZoneInfo('Asia/Jerusalem')).timestamp()


class UpdateScheduleTests(unittest.TestCase):
    def test_daily_boundary_and_israel_dst(self):
        for now, expected in (
            ('2026-10-18T04:59:59','2026-10-18T05:00:00'),
            ('2026-10-18T05:00:00','2026-10-19T05:00:00'),
            ('2026-10-18T20:00:00','2026-10-19T05:00:00'),
            ('2026-03-26T05:00:00','2026-03-27T05:00:00'),
            ('2026-10-24T05:00:00','2026-10-25T05:00:00'),
        ):
            with self.subTest(now=now):
                self.assertEqual(next_update_at(local_time(now)),local_time(expected))
        self.assertEqual(next_update_at(local_time('2026-03-26T05:00:00'))-local_time('2026-03-26T05:00:00'),23*3600)
        self.assertEqual(next_update_at(local_time('2026-10-24T05:00:00'))-local_time('2026-10-24T05:00:00'),25*3600)

    def run_worker(self, now, *, manual=False, suspend=False, newer=False, failed=False, stop_before_due=False, platform='linux'):
        clock=[local_time(now)]
        stop=threading.Event()
        wake=threading.Event()
        if manual:
            wake.set()
        shutdown=Mock()
        request_times=[]

        def wait(seconds):
            self.assertLessEqual(seconds,60)
            if stop_before_due:
                stop.set()
            elif suspend:
                clock[0]=local_time('2026-10-21T08:00:00')
            else:
                clock[0]+=seconds
            return False

        def release(url):
            request_times.append(clock[0])
            if failed:
                stop.set()
                raise OSError('offline')
            return {'tag_name':'v99.0.0'}

        def download(root, release):
            stop.set()
            return Path('/tmp/verified-update') if newer else None

        def install(staged, callback):
            callback()

        with tempfile.TemporaryDirectory() as folder, \
             patch('windows_update.sys.platform',platform), \
             patch('windows_update.supported',return_value=True), \
             patch('linux_launcher.app_root',return_value=Path(folder)), \
             patch.dict('os.environ',{'LOCALAPPDATA':folder}), \
             patch.object(updates,'_state',{'version':'test','phase':'idle'}), \
             patch.object(updates,'_wake',wake), \
             patch.object(wake,'wait',side_effect=wait), \
             patch('windows_update.time.time',side_effect=lambda:clock[0]), \
             patch('windows_update.get_json',side_effect=release), \
             patch('windows_update.download_release',side_effect=download), \
             patch('windows_update.install',side_effect=install) as installed:
            updates.worker(stop,shutdown)
            state=updates.status()
        return request_times,shutdown,installed,state

    def test_process_checks_at_five_and_installs_without_browser(self):
        times,shutdown,installed,state=self.run_worker('2026-10-18T04:59:30',newer=True)
        self.assertEqual(times,[local_time('2026-10-18T05:00:00')])
        installed.assert_called_once()
        shutdown.assert_called_once()
        self.assertEqual(state['phase'],'installing')

    def test_manual_check_wakes_process_before_daily_deadline(self):
        times,shutdown,installed,state=self.run_worker('2026-10-18T14:00:00',manual=True)
        self.assertEqual(times,[local_time('2026-10-18T14:00:00')])
        self.assertEqual(state['next_check_at'],local_time('2026-10-19T05:00:00')*1000)
        shutdown.assert_not_called()
        installed.assert_not_called()

    def test_starting_during_day_waits_for_daily_check_and_stops_promptly(self):
        times,shutdown,installed,_=self.run_worker('2026-10-18T14:00:00',stop_before_due=True)
        self.assertEqual(times,[])
        shutdown.assert_not_called()
        installed.assert_not_called()

    def test_resume_after_missed_deadline_checks_once(self):
        times,shutdown,installed,state=self.run_worker('2026-10-18T04:59:30',suspend=True)
        self.assertEqual(times,[local_time('2026-10-21T08:00:00')])
        self.assertEqual(state['next_check_at'],local_time('2026-10-22T05:00:00')*1000)
        shutdown.assert_not_called()

    def test_failed_check_keeps_app_running_and_schedules_next_day(self):
        times,shutdown,installed,state=self.run_worker('2026-10-18T04:59:30',failed=True)
        self.assertEqual(len(times),1)
        self.assertEqual(state['phase'],'error')
        self.assertEqual(state['next_check_at'],local_time('2026-10-19T05:00:00')*1000)
        shutdown.assert_not_called()
        installed.assert_not_called()

    def test_windows_keeps_startup_and_hourly_schedule(self):
        times,shutdown,installed,state=self.run_worker('2026-10-18T14:00:00',platform='win32')
        self.assertEqual(times,[local_time('2026-10-18T14:00:00')])
        self.assertEqual(state['next_check_at'],local_time('2026-10-18T15:00:00')*1000)
        shutdown.assert_not_called()
