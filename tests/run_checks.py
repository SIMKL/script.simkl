"""Exercises the add-on's logic outside Kodi with stubbed xbmc modules: queue ordering, account binding, auth races,
importer policy, HTTP parsing, playback sampling, identification, the context actions, the QR code.
Run with any Python 3.8+: python tests/run_checks.py (exit code 1 on a failure)."""
import json
import os
import sys
import tempfile
import threading
import time
import types

sys.dont_write_bytecode = True
PROFILE = tempfile.mkdtemp()
SETTINGS = {}
RPC = {}            # method -> result for the stubbed JSON-RPC
WINDOW = {}         # home window properties


def _stub(name, **attrs):
    module = types.ModuleType(name)
    module.__dict__.update(attrs)
    sys.modules[name] = module
    return module


class _Addon:
    def __init__(self, *args):
        pass

    def getAddonInfo(self, key):
        return {'version': '4.0.0', 'path': os.getcwd(), 'icon': '', 'profile': PROFILE}.get(key, '')

    def getSetting(self, key):
        return SETTINGS.get(key, '')

    def setSetting(self, key, value):
        SETTINGS[key] = value

    def getLocalizedString(self, sid):
        return 'str%d {0}' % sid


PICK = [-1]
PRESELECT = [-1]
SELECTS = [0]       # select dialogs shown
NOTIFICATIONS = []  # the toasts of the short-lived scripts: (heading, message, ...)
YESNO = []          # the three-button questions shown, with their arguments
ANSWER = [-1]       # what the next one answers: 1 yes, 2 custom, anything else no/closed


class _Dialog:
    def notification(self, *args):
        NOTIFICATIONS.append(args)

    def select(self, heading, options, preselect=-1):
        SELECTS[0] += 1
        PRESELECT[0] = preselect
        return PICK[0]

    def yesnocustom(self, heading, message, customlabel, nolabel='', yeslabel='', autoclose=0):
        YESNO.append((heading, message, customlabel, nolabel, yeslabel, autoclose))
        return ANSWER[0]


class _Monitor:
    def waitForAbort(self, seconds):
        return False

    def abortRequested(self):
        return False


class _Window:
    def __init__(self, wid):
        pass

    def getProperty(self, name):
        return WINDOW.get(name, '')

    def setProperty(self, name, value):
        WINDOW[name] = value

    def clearProperty(self, name):
        WINDOW.pop(name, None)


def _rpc(request):
    req = json.loads(request)
    return json.dumps({'id': req.get('id'), 'result': RPC.get(req['method'], {})})


LABELS = {'System.BuildVersion': '21.2'}
BUILTINS = []
IDLE = [0]          # seconds since Kodi's last input
# conditions the code asks Kodi about; anything not listed (System.InternetState ...) reads as true
CONDS = {'Player.Paused': False, 'Player.HasVideo': False, 'Player.HasVideo + !Player.Paused': False, 'Library.IsScanningVideo': False, 'Window.IsVisible(movieinformation)': False}
_stub('xbmc', log=lambda *a, **k: None, LOGDEBUG=0, LOGINFO=1, LOGWARNING=2, LOGERROR=3, getInfoLabel=lambda label: LABELS.get(label, ''),
      getLocalizedString=lambda sid: 'Trailer' if sid == 20410 else 'str%d' % sid, getCondVisibility=lambda cond: CONDS.get(cond, True),
      executeJSONRPC=_rpc, executebuiltin=BUILTINS.append, getGlobalIdleTime=lambda: IDLE[0], Monitor=_Monitor, Player=object)
_stub('xbmcaddon', Addon=_Addon)
_stub('xbmcgui', Dialog=_Dialog, Window=_Window, WindowXMLDialog=object, DialogProgressBG=object)
_stub('xbmcvfs', translatePath=lambda p: p, exists=lambda p: os.path.isdir(p), mkdirs=lambda p: os.makedirs(p, exist_ok=True))

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
SETTINGS['token'] = 'legacyV1'   # a 3.x install: the pin-flow token sits in the Kodi settings
from resources.lib import history as h  # noqa: E402
from resources.lib import importer as imp  # noqa: E402
from resources.lib import player as playerlib  # noqa: E402
from resources.lib import scrobbler as scr  # noqa: E402
from resources.lib import settings, store, utils  # noqa: E402
from resources.lib.api import ApiError  # noqa: E402
from resources.lib.auth import Auth, InvalidToken  # noqa: E402
from resources.lib.marks import Marks  # noqa: E402

ITEM = {'kind': 'movie', 'key': 'movie:1', 'dbid': 1, 'title': 'X', 'year': 2020, 'ids': {'imdb': 'tt1'}, 'label': 'X (2020)'}
failures = []


def check(name, condition):
    print(('ok   ' if condition else 'FAIL ') + name)
    if not condition:
        failures.append(name)


def ident(**values):
    settings.put_identity(values)


ASKS = []           # the movie unmarks handed to the (absent) service for their question
real_notify = utils.notify


def fresh_queue():
    q = h.History(api=None, auth=None, ask=ASKS.append)
    q.entries = []
    q.unmarked = {}
    q.unmarked_account = ''
    q.sent_unmarks = {}
    q.reask = []
    q.window_unmarks = []
    return q


def kinds(queue):
    return [e['kind'] for e in queue.entries]


def answer_all(queue, choice='remove'):
    """The user's answer to every open movie-unmark question."""
    for entry in list(queue.entries):
        if entry.get('asking'):
            queue.answer_unmark(entry['id'], choice)


def queued_watch(queue, watched_ts):
    """A watch whose stop failed: recorded ahead of the stop, then released to the queue."""
    entry_id = queue.watch_event(ITEM, watched_ts, False)
    if entry_id:
        queue.release(entry_id, toast=False)
    return entry_id


# --- identity store: the 3.x token moves out of the Kodi settings on the first access ---
check('identity: legacy token migrated into identity.json', settings.token() == 'legacyV1' and SETTINGS['token'] == '' and os.path.exists(os.path.join(PROFILE, 'identity.json')))
ident(token='simkl_at_A', account_id='A')
check('identity: writes go to the file, not to the Kodi settings', json.load(open(os.path.join(PROFILE, 'identity.json'))).get('account_id') == 'A' and 'account_id' not in SETTINGS)

# --- the settings page shows Log in / Reconnect / Log out by a state the service keeps in a Kodi setting ---
states = []
for token in ('', 'legacyV1', 'simkl_at_A'):
    ident(token=token, account_name='Ann')
    Auth(None).update_status()
    states.append(SETTINGS.get('login_state'))
check('settings: login_state 0 = not connected, 1 = old sign-in, 2 = new sign-in', states == ['0', '1', '2'])
ident(token='simkl_at_A', account_id='A', account_name='')

# --- a late watch acknowledgement must not delete a newer unmark ---
q = fresh_queue()
eid = q.watch_event(ITEM, 100.0, False)
q.add_unmark(ITEM, explicit=True)
check('unmark supersedes the in-flight watch', kinds(q) == [h.UNMARK])
q.ack(eid)
q.live_watch_succeeded(ITEM['key'], 100.0)
check('the newer unmark survives the watch acknowledgement', kinds(q) == [h.UNMARK])

# --- another account's queued actions are not this one's to cancel (a file action that finished after a re-login) ---
q = fresh_queue()
q.add_mark(ITEM, account='B')
q.add_mark(ITEM, account='A')
q.add_mark(ITEM, account='A')
check('queue: a mark supersedes only the same account\'s mark', [e['account'] for e in q.entries] == ['B', 'A'])
q.add_unmark(ITEM, explicit=True, account='B')
check('queue: an explicit unmark of one account leaves the other\'s mark', [(e['account'], e['kind']) for e in q.entries] == [('A', h.MARK), ('B', h.UNMARK)])
check('queue: the other account\'s later unmark does not refuse this one\'s watch', q.watch_event(ITEM, time.time() - 10, False, account='A') is not None and kinds(q) == [h.MARK, h.UNMARK, h.WATCH])
q.live_watch_succeeded(ITEM['key'], time.time(), 'A')
check('queue: a live watch of one account drops no unmark of the other', kinds(q) == [h.MARK, h.UNMARK, h.WATCH])
q = fresh_queue()
q.add_mark(ITEM, account='')
q.add_unmark(ITEM, explicit=True, account='A')
check('queue: a mark recorded before the account id was known is superseded like this account\'s (the flush sends it as such)', kinds(q) == [h.UNMARK])

# --- an older released watch is refused, a newer one drops the unmark ---
q = fresh_queue()
q.add_unmark(ITEM, explicit=True)
unmark_time = q.entries[0]['time']
check('an older released watch is refused', queued_watch(q, unmark_time - 3600) is None and kinds(q) == [h.UNMARK])
newer = queued_watch(q, unmark_time + 1)
check('a newer watch drops the older unmark', newer is not None and kinds(q) == [h.WATCH] and not q.entries[0]['inflight'])

# --- restart: in-flight entries are flushed like failed ones ---
q = fresh_queue()
q.watch_event(ITEM, 100.0, False)
check('entry saved in flight', q.entries[0]['inflight'])
q2 = h.History(api=None, auth=None)
check('after a restart the entry is no longer in flight', len(q2.entries) == 1 and not q2.entries[0]['inflight'] and q2.due is not None)

# --- a DELIVERED unmark still blocks an older recovered watch, across restarts ---
q = fresh_queue()
q.add_unmark(ITEM, explicit=True)
unmark = q.entries[0]
q._delivered([unmark], unmark=True)
check('delivered unmark remembered', q.entries == [] and q.unmarked.get(ITEM['key']) == unmark['time'])
check('a released watch older than the delivered unmark is refused', queued_watch(q, unmark['time'] - 60) is None and q.entries == [])
q3 = h.History(api=None, auth=None)
check('the stamp survives a restart', q3.unmarked.get(ITEM['key']) == unmark['time'] and queued_watch(q3, unmark['time'] - 60) is None)
check('a watch newer than the delivered unmark is recorded', queued_watch(q3, unmark['time'] + 60) is not None)

# --- a watch keeps the account of its playback ---
q = fresh_queue()
ident(account_id='B')
q.watch_event(ITEM, 100.0, False, account='A')
check('watch event recorded under the playback\'s account, not the current one', q.entries[0]['account'] == 'A')
check('it is never delivered under B', q._eligible() == [] and q.entries == [])
q = fresh_queue()
q.add_mark(ITEM, account='A')
q.add_unmark(dict(ITEM, key='movie:2', dbid=2), explicit=True, account='A')
check('files: a mark or removal issued under A before a login to B stays A\'s and is never sent under B',
      [e['account'] for e in q.entries] == ['A', 'A'] and q._eligible() == [] and q.entries == [])
ident(account_id='A')

# --- nothing bound to an account goes out while the account is unverified ---
q = fresh_queue()
q.add_mark(ITEM)
ident(account_id='')
submitted = []
q.due = 0
q.tick(1e12, types.SimpleNamespace(submit=submitted.append))
check('the flush waits while the account is unverified', submitted == [] and q.due == 0)
ident(account_id='A')
q.tick(1e12, types.SimpleNamespace(submit=submitted.append))
check('and runs once it is verified', len(submitted) == 1)
q.flushing = False

q = fresh_queue()
ident(account_id='')
q.add_mark(ITEM)
q.on_login('', 'B')
check('a first login to B drops entries recorded under an unknown account', q.entries == [])
q = fresh_queue()
ident(account_id='A')
q.add_mark(ITEM)
q.on_login('A', 'A')
check('a re-login to the same account keeps them', kinds(q) == [h.MARK])


class FakeApi:
    def request(self, method, path, body=None, token=None, timeout=30, client_id=None):
        return 200, {'access_token': 'simkl_at_renewed', 'refresh_token': 'r', 'expires_in': 604800}


ident(token='simkl_at_A', refresh_token='rA', token_expires='9999999999', account_id='')
auth = Auth(FakeApi())
sent = []


def op(token):
    sent.append(token)
    return 'ok'


for account in ('A', ''):
    try:
        auth.authorized(op, account)
        check('bound operation (%r) refused while unverified' % account, False)
    except InvalidToken:
        check('bound operation (%r) refused while unverified' % account, sent == [])
check('the account lookup itself (unbound) goes out', auth.authorized(op, None) == 'ok' and sent == ['simkl_at_A'])

# --- an operation of account A is never sent under account B ---
ident(account_id='A')
sent.clear()
check('same account: sent with the stored token', auth.authorized(op, 'A') == 'ok' and sent == ['simkl_at_A'])
ident(account_id='B', token='simkl_at_B')
sent.clear()
try:
    auth.authorized(op, 'A')
    check('operation of A refused after a login to B', False)
except InvalidToken:
    check('operation of A refused after a login to B', sent == [])
check('unknown-account operation goes out under the verified account', auth.authorized(op, '') == 'ok' and sent == ['simkl_at_B'])

ident(token='simkl_at_A', account_id='A')
sent.clear()


def op_401_then_switch(token):
    sent.append(token)
    ident(token='simkl_at_B', account_id='B')
    raise ApiError(401, 'user_token_failed')


try:
    auth.authorized(op_401_then_switch, 'A')
    check('a 401 of A is not retried with B\'s token', False)
except InvalidToken:
    check('a 401 of A is not retried with B\'s token', sent == ['simkl_at_A'] and settings.token() == 'simkl_at_B')

ident(token='simkl_at_A', account_id='A')
sent.clear()


def op_401_once(token):
    sent.append(token)
    if token == 'simkl_at_A':
        ident(token='simkl_at_A2')
        raise ApiError(401, 'user_token_failed')
    return 'ok'


check('same account renewed meanwhile: retried once with the fresh token', auth.authorized(op_401_once, 'A') == 'ok' and sent == ['simkl_at_A', 'simkl_at_A2'])

ident(token='legacyV1', refresh_token='', account_id='A')
sent.clear()


def op_401(token):
    sent.append(token)
    raise ApiError(401, 'user_token_failed')


try:
    auth.authorized(op_401, 'A')
    check('V1 token rejected: InvalidToken', False)
except InvalidToken:
    check('V1 token rejected: cleared, one attempt', sent == ['legacyV1'] and settings.token() == '')


# --- importer failure policy ---
class FakeMarks:
    def guard(self, *args):
        pass


ident(token='simkl_at_A', account_id='A')
SETTINGS['continue_watching'] = 'true'
ROW = {'kind': 'movie', 'dbid': 1, 'key': 'movie:1', 'playcount': 0, 'lastplayed': '', 'dateadded': '2024-01-01 00:00:00', 'runtime': 3600, 'resume': 0, 'userrating': 0}
PAUSE = [{'type': 'movie', 'progress': 50, 'paused_at': '2026-01-01T10:00:00Z', 'movie': {'ids': {'imdb': 'tt1'}}}]
ACTIVITIES = {'movies': {'playback': '2026-01-02T00:00:00Z'}}


def index_with_movie():
    index = imp.LibraryIndex()
    index.movies_read = index.shows_read = True
    index.movies['imdb:tt1'] = [dict(ROW)]
    return index


def batch_sequence(*answers):
    queue = list(answers)
    return lambda calls: queue.pop(0) if queue else [{'error': 'unexpected'}] * len(calls)


details_ok = [{'id': 0, 'result': {'moviedetails': {'playcount': 0, 'lastplayed': ''}}}]
write_error = [{'id': 0, 'error': {'code': -32602, 'message': 'Invalid params.'}}]
write_ok = [{'id': 0, 'result': 'OK'}]

ledger = h.History(api=None, auth=None)
ledger.entries, ledger.unmarked, ledger.window_unmarks = [], {}, []
importer = imp.Importer(api=None, auth=None, marks=FakeMarks(), history=ledger)
importer._get = lambda path, timeout=30: PAUSE
real_batch = utils.jsonrpc_batch

utils.jsonrpc_batch = batch_sequence(details_ok, write_error)
job, state = imp.Job(False), {}
importer._continue_watching(job, index_with_movie(), state, ACTIVITIES)
check('a rejected resume write is counted and the playback cursor does not move', job.write_failures == 1 and job.resume_updated == 0 and 'playback_cursor' not in state)

utils.jsonrpc_batch = batch_sequence(details_ok, write_ok)
job, state = imp.Job(False), {}
importer._continue_watching(job, index_with_movie(), state, ACTIVITIES)
check('a successful resume write advances the playback cursor', job.write_failures == 0 and job.resume_updated == 1 and state.get('playback_cursor') == '2026-01-02T00:00:00Z')

# --- the pauses come whole, without date_from: a cursor only says whether to look at all ---
playback_paths = []
importer._get = lambda path, timeout=30: playback_paths.append(path) or []
state = {}
importer._continue_watching(imp.Job(False), index_with_movie(), state, {})
no_activity = (list(playback_paths), state.get('playback_cursor'))
importer._continue_watching(imp.Job(False), index_with_movie(), state, {})
quiet = len(playback_paths)
importer._continue_watching(imp.Job(False), index_with_movie(), state, ACTIVITIES)
moved = (len(playback_paths), state.get('playback_cursor'))
importer._continue_watching(imp.Job(False), index_with_movie(), state, ACTIVITIES)
unchanged = len(playback_paths)
importer._continue_watching(imp.Job(False), index_with_movie(), state, ACTIVITIES, grown=True)
grew = len(playback_paths)
importer.playback_full = True
importer._continue_watching(imp.Job(False), index_with_movie(), state, ACTIVITIES)
switched_on = (len(playback_paths), importer.playback_full)
check('continue watching: the whole list is asked for, never with date_from; a first look without any playback activity stamps the epoch and the next quiet look asks nothing',
      no_activity == (['/sync/playback'], imp.EPOCH) and quiet == 1 and all(p == '/sync/playback' for p in playback_paths))
check('continue watching: a moved playback activity, a grown library and the switch just turned on each ask again, an unchanged activity does not',
      moved == (2, '2026-01-02T00:00:00Z') and unchanged == 2 and grew == 3 and switched_on == (4, False))
importer._get = lambda path, timeout=30: PAUSE

utils.jsonrpc_batch = batch_sequence(write_error)
job = imp.Job(False)
importer._write(job, [(dict(ROW), {'playcount': 1})])
check('a failed pre-write read is a failure, not "changed"', job.write_failures == 1 and job.changed == 0)

utils.jsonrpc_batch = batch_sequence([{'id': 0, 'result': {'moviedetails': {'playcount': 1, 'lastplayed': '2026-01-01 10:00:00'}}}])
job = imp.Job(False)
importer._write(job, [(dict(ROW), {'playcount': 1})])
check('an item changed in Kodi meanwhile is skipped, not written', job.changed == 1 and job.write_failures == 0)

utils.jsonrpc_batch = lambda calls: None
job = imp.Job(False)
importer._write(job, [(dict(ROW), {'playcount': 1})])
check('a refused batch counts every item as failed', job.write_failures == 1)
utils.jsonrpc_batch = real_batch

real_jsonrpc = utils.jsonrpc
utils.jsonrpc = lambda method, params=None: None
try:
    imp.LibraryIndex().read_movies()
    check('an unreadable library fails the run', False)
except imp.RunError:
    check('an unreadable library fails the run', True)
utils.jsonrpc = real_jsonrpc

job = imp.Job(True)
job.episodes_marked, job.refreshed, job.resume_updated, job.movies_missing, job.shows_missing, job.episodes_missing = 5, 4, 3, 778, 2811, 13
short, full, missing = imp.Importer._summary(job, False)
check('sync: the settings line is the short marks figure plus one suffix, the toast keeps every sentence, the misses are apart',
      short == 'str32283 5; str32284 4' and full.startswith('str32248 {0} str32230 0') and 'str32259 4' in full and 'str32243 3' in full and missing.startswith('str32280'))
check('sync: without refreshed dates the suffix is the resume points', imp.Importer._summary(dict_job := (lambda j: (setattr(j, 'resume_updated', 3), j)[1])(imp.Job(False)), False)[0] == 'str32283 0; str32285 3')
check('sync: nothing fetched -> no missing counts', imp.Importer._summary(imp.Job(False), True)[2] == '')

RPC['VideoLibrary.GetMovies'] = {'limits': {'start': 0, 'end': 1, 'total': 3}}
RPC['VideoLibrary.GetEpisodes'] = {'limits': {'start': 0, 'end': 1, 'total': 7}}
check('import: the library totals, all and unwatched, come from the limits of one-item answers',
      imp.Importer._library_totals() == {'movies': 3, 'movies_unwatched': 3, 'episodes': 7, 'episodes_unwatched': 7})
RPC.pop('VideoLibrary.GetMovies')
RPC.pop('VideoLibrary.GetEpisodes')

# --- Simkl ratings into Kodi: with the all-items rows on a full run, through /sync/ratings on an incremental one ---
job = imp.Job(False)
row = dict(ROW)
imp.Importer._rate(job, row, {'user_rating': 8})
imp.Importer._rate(job, row, {'user_rating': 8})
imp.Importer._rate(job, dict(ROW, dbid=2, userrating=7), {'user_rating': 7})
imp.Importer._rate(job, None, {'user_rating': 5})
imp.Importer._rate(job, dict(ROW, dbid=3), {'user_rating': None})
imp.Importer._rate(job, dict(ROW, dbid=4), {'user_rating': 11})
check('ratings: only a different 1-10 Simkl rating of a matched item is written, once', job.rating_writes == [(row, {'userrating': 8})])
calls_seen = []
LIVE_RATING = [0]   # what Kodi answers to the rating re-read before a write


def rating_batch(calls):
    answers = []
    for method, params in calls:
        if method.startswith('VideoLibrary.Get'):
            answers.append({'result': {key: {'userrating': LIVE_RATING[0], 'playcount': 0, 'lastplayed': ''} for key in ('moviedetails', 'episodedetails', 'tvshowdetails')}})
        else:
            calls_seen.append((method, params))
            answers.append({'result': 'OK'})
    return answers


utils.jsonrpc_batch = rating_batch
importer._write_ratings(job)
check('ratings: one batch of Set*Details with the user rating, the index row follows',
      calls_seen == [('VideoLibrary.SetMovieDetails', {'userrating': 8, 'movieid': 1})] and job.ratings_updated == 1 and job.write_failures == 0 and row['userrating'] == 8 and job.rating_writes == [])
index = index_with_movie()
index.shows[5] = {'kind': 'tvshow', 'dbid': 5, 'userrating': 0}
index.series['tvdb:9'] = [5]
job = imp.Job(False)
importer._apply_show(job, index, {'show': {'ids': {'tvdb': '9'}, 'title': 'S'}, 'user_rating': 6, 'seasons': []}, False, 'watching')
calls_seen.clear()
importer._write_ratings(job)
check('ratings: a show\'s rating goes to SetTVShowDetails', calls_seen == [('VideoLibrary.SetTVShowDetails', {'userrating': 6, 'tvshowid': 5})] and index.shows[5]['userrating'] == 6)
job = imp.Job(False)
importer._apply(job, index, 'movies', [{'movie': {'ids': {'imdb': 'tt1'}}, 'status': 'plantowatch', 'user_rating': 7}])
importer._apply(job, index, 'shows', [{'show': {'ids': {'tvdb': '9'}, 'title': 'S'}, 'status': 'plantowatch', 'user_rating': 4, 'seasons': []}])
check('ratings: a full run rates plan-to-watch titles too, nothing is marked',
      [(r['dbid'], f['userrating']) for r, f in job.rating_writes] == [(1, 7), (5, 4)] and job.writes == [] and job.movies_marked == job.episodes_marked == 0)
job = imp.Job(False)
importer._apply(job, index, 'movies', [{'movie': {'ids': {'imdb': 'tt1'}}, 'status': 'notinteresting', 'user_rating': 3}])
check('ratings: a dropped movie is rated, not marked', [(r['dbid'], f['userrating']) for r, f in job.rating_writes] == [(1, 3)] and job.writes == [] and job.movies_marked == 0)
asked = []
importer._get = lambda path, timeout=30: asked.append(path) or {'movies': [{'movie': {'ids': {'imdb': 'tt1'}}, 'user_rating': 9}]}
job = imp.Job(False)
importer._changed_ratings(job, index_with_movie(), {'rated': {'movies': '2026-10-01T00:00:00Z'}}, {'movies': '2026-10-02T00:00:00Z', 'shows': '', 'anime': ''}, {'movies': {}, 'shows': {}, 'anime': {}})
check('ratings: an incremental run asks /sync/ratings from the stored stamp when rated_at moved',
      asked == ['/sync/ratings?date_from=2026-09-30T23%3A59%3A00Z'] and [(r['dbid'], f) for r, f in job.rating_writes] == [(1, {'userrating': 9})])
asked.clear()
job = imp.Job(False)
importer._changed_ratings(job, index_with_movie(), {'rated': {'movies': '2026-10-02T00:00:00Z'}}, {'movies': '2026-10-02T00:00:00Z', 'shows': '', 'anime': ''}, {'movies': {}, 'shows': {}, 'anime': {}})
check('ratings: nothing asked when no rated_at stamp moved', asked == [] and job.rating_writes == [])
SETTINGS['import_ratings'] = 'false'
job = imp.Job(False)
imp.Importer._rate(job, dict(ROW), {'user_rating': 8})
check('ratings: the switch off writes nothing', job.rating_writes == [])
SETTINGS.pop('import_ratings')
# --- the rated stamps move only while the switch is on: switched on later, the next run asks for every rating ---
RPC['VideoLibrary.GetMovies'] = {'limits': {'start': 0, 'end': 1, 'total': 1}}
RPC['VideoLibrary.GetEpisodes'] = {'limits': {'start': 0, 'end': 1, 'total': 0}}
asked.clear()
importer._get = lambda path, timeout=30: asked.append(path) or ({'movies': {'rated_at': '2026-10-02T00:00:00Z', 'completed': '2026-10-01T00:00:00Z'}} if path == '/sync/activities' else {})
importer._save({})
SETTINGS['import_ratings'] = 'false'
importer._run('check', False, False)
off = importer._load()
SETTINGS.pop('import_ratings')
importer._run('check', False, False)
check('ratings: no rated stamp while the switch is off; switched on, the next run asks for every rating of the type, rated rows only',
      not off.get('rated') and off.get('full_import_at') and '/sync/ratings/movies/1,2,3,4,5,6,7,8,9,10' in asked and importer._load().get('rated') == {'movies': '2026-10-02T00:00:00Z'})
RPC.pop('VideoLibrary.GetMovies')
RPC.pop('VideoLibrary.GetEpisodes')
# --- a type too big for one call comes per status, without plan to watch: its ratings then come through /sync/ratings/<type> ---
RPC['VideoLibrary.GetMovies'] = {'limits': {'start': 0, 'end': 1, 'total': 1}}
RPC['VideoLibrary.GetEpisodes'] = {'limits': {'start': 0, 'end': 1, 'total': 0}}
RPC['VideoLibrary.GetTVShows'] = {'tvshows': [{'tvshowid': 5, 'uniqueid': {'tvdb': '9'}, 'userrating': 0}]}
asked.clear()
calls_seen.clear()
utils.jsonrpc_batch = rating_batch


def big_shows(path, timeout=30):
    asked.append(path)
    if path == '/sync/activities':
        return {'tv_shows': {'rated_at': '2026-10-02T00:00:00Z', 'completed': '2026-10-01T00:00:00Z'}}
    if path.startswith('/sync/all-items/shows?'):
        raise ApiError(400, 'max_items', 'too many')
    if path == '/sync/ratings/shows/1,2,3,4,5,6,7,8,9,10':
        return {'shows': [{'show': {'ids': {'tvdb': '9'}, 'title': 'S'}, 'status': 'plantowatch', 'user_rating': 8}]}
    return {}


def too_many(path, timeout=30):
    raise ApiError(400, 'max_items', 'too many')


importer._get = big_shows
importer.auth = Auth(None)
importer._save({})
importer._run('reset', False, False)
check('ratings: a type fetched per status gets its ratings from one /sync/ratings/<type>/1..10 call, the stamp follows',
      any(p.startswith('/sync/all-items/shows/completed?') for p in asked) and '/sync/ratings/shows/1,2,3,4,5,6,7,8,9,10' in asked and not any(p.startswith('/sync/ratings/movies') or p.startswith('/sync/ratings/anime') for p in asked)
      and calls_seen == [('VideoLibrary.SetTVShowDetails', {'userrating': 8, 'tvshowid': 5})] and importer._load().get('rated') == {'shows': '2026-10-02T00:00:00Z'})
importer._get = too_many
check('ratings: more rated items than one call allows are skipped (None) with a warning, not a failed run', importer._all_ratings('shows') is None)
RPC.pop('VideoLibrary.GetMovies')
RPC.pop('VideoLibrary.GetEpisodes')
RPC.pop('VideoLibrary.GetTVShows')
importer.auth = None
importer.last_finish = 0
importer._get = lambda path, timeout=30: PAUSE
utils.jsonrpc_batch = real_batch

# --- Continue watching switched on: its playback-only run waits for a Sync pressed on the same page and arms no cooldown ---
started = []
real_start = importer.start
importer.start = lambda mode='check', manual=True, scheduled=False, force=False: started.append((mode, manual, force)) or True
importer.playback_full = False
importer.request_playback_full()
importer.start_due()
at_once = (list(started), importer.playback_full, importer.playback_due > time.time())
importer.playback_due = time.time() - 1
importer.playback_full = False   # a manual check ran meanwhile and fetched the pauses itself
importer.start_due()
covered = (list(started), importer.playback_due)
importer.request_playback_full()
importer.playback_due = time.time() - 1
importer.start_due()
due = list(started)
importer.start = real_start
check('continue watching: the switch starts no run at once (%d s), nothing once a run fetched the pauses meanwhile, else the playback run once due' % imp.PLAYBACK_DELAY,
      at_once == ([], True, True) and covered == ([], 0.0) and due == [('playback', False, True)])
RPC['VideoLibrary.GetMovies'] = {'limits': {'start': 0, 'end': 1, 'total': 1}}
RPC['VideoLibrary.GetEpisodes'] = {'limits': {'start': 0, 'end': 1, 'total': 0}}
importer.auth = Auth(None)
importer._get = lambda path, timeout=30: {} if path == '/sync/activities' else PAUSE
importer._save({})
importer.last_finish = 0
importer._run('playback', False, False)
after_playback = (importer.last_finish, importer._load().get('last_summary'))
importer._run('check', False, False)
after_check = importer.last_finish
RPC.pop('VideoLibrary.GetMovies')
RPC.pop('VideoLibrary.GetEpisodes')
importer.auth = None
importer.last_finish = 0
importer._get = lambda path, timeout=30: PAUSE
check('continue watching: a playback-only run (completed, not failed) leaves the cooldown unarmed, a check arms it',
      after_playback[0] == 0 and after_playback[1] and after_playback[1] != 'str32231' and after_check > 0)

# a resume point Kodi set after the Simkl watch stays; no Simkl date -> Kodi's added date
job = imp.Job(False)
imp.Importer._mark(job, dict(ROW, resume=600, lastplayed='2026-06-01 20:00:00'), utils.parse_stamp('2026-01-01T10:00:00Z'), None)
check('a newer local resume point is kept (playcount only)', job.writes[0][1] == {'playcount': 1})
job = imp.Job(False)
imp.Importer._mark(job, dict(ROW), None, None)
check('no Simkl date -> last played = Kodi added date, resume cleared', job.writes[0][1] == {'playcount': 1, 'resume': {'position': 0, 'total': 0}, 'lastplayed': '2024-01-01 00:00:00'})


# --- an obsolete job of the old account's session must not send anything under the new account ---
class RefusingAuth:
    def logged_in(self):
        return True

    def authorized(self, call, account=None):
        raise InvalidToken()


class CountingAuth:
    def __init__(self, answer):
        self.answer = answer
        self.calls = []

    def logged_in(self):
        return True

    def authorized(self, call, account=None):
        self.calls.append(account)
        return self.answer


class FakeSender:
    def __init__(self):
        self.jobs = []

    def submit(self, job):
        self.jobs.append(job)


class FakePlayer:
    def __init__(self, position=0.0, total=0.0, path='A'):
        self.position, self.total, self.path = position, total, path

    def isPlayingVideo(self):
        return True

    def sample(self):
        return self.position, self.total

    def playing_file(self):
        return self.path


ident(token='simkl_at_A', account_id='A')
counting = CountingAuth({'action': 'start'})
scrobbler = scr.Scrobbler(api=types.SimpleNamespace(call=lambda *a, **k: None), auth=counting, player=FakePlayer(), sender=FakeSender(), history=fresh_queue(), marks=FakeMarks())
session = scr.Session(1)
session.item = dict(ITEM)
session.started = True
scrobbler.session = session
ident(account_id='B')
scrobbler.on_login('A', 'B')
check('the running session of A is ignored after the login to B', session.ignored)
scrobbler._send(session, 'start', 10.0, None)
check('its queued start sends nothing', counting.calls == [])
session2 = scr.Session(2)
session2.account = 'A'
session2.item = dict(ITEM)
session2.started = True
scrobbler._send(session2, 'start', 10.0, None)
check('a request is bound to the session\'s account, never the current one', counting.calls == ['A'])
WINDOW.clear()


# --- a delayed account lookup of A must not overwrite B's identity ---
class SwitchingApi:
    def call(self, method, path, body=None, token=None, timeout=30):
        ident(token='simkl_at_B', account_id='B')
        return {'user': {'name': 'a'}, 'account': {'id': 'A', 'type': 'free'}}

    def request(self, *args, **kwargs):
        raise AssertionError('no renewal expected')


ident(token='simkl_at_A', refresh_token='rA', token_expires='9999999999', account_id='A')
auth = Auth(SwitchingApi())
check('a stale account answer is discarded', auth.fetch_account() is None and settings.account_id() == 'B' and settings.token() == 'simkl_at_B')


class AccountApi:
    def call(self, method, path, body=None, token=None, timeout=30):
        return {'user': {'name': 'Ann'}, 'account': {'id': 7, 'type': 'Pro'}}

    def request(self, *args, **kwargs):
        raise AssertionError('no renewal expected')


ident(token='simkl_at_A', refresh_token='rA', token_expires='9999999999', account_id='')
writes = []
real_write = settings._write_identity
settings._write_identity = lambda state: writes.append(dict(state)) or real_write(state)
fetched = Auth(AccountApi()).fetch_account()
settings._write_identity = real_write
check('fetch_account stores id (as a string), name and plan in one write and returns them',
      len(writes) == 1 and writes[0]['account_id'] == '7' and writes[0]['account_name'] == 'Ann' and writes[0]['account_plan'] == 'pro'
      and fetched == {'account_id': '7', 'account_name': 'Ann', 'account_plan': 'pro'} and settings.is_pro())


# --- a renewal answered after a login to B must not overwrite B's tokens ---
class RenewDuringLoginApi:
    def request(self, method, path, body=None, token=None, timeout=30, client_id=None):
        ident(token='simkl_at_B', refresh_token='rB', account_id='B')
        return 200, {'access_token': 'simkl_at_A2', 'refresh_token': 'rA', 'expires_in': 604800}


ident(token='simkl_at_A', refresh_token='rA', token_expires='9999999999', account_id='A')
auth = Auth(RenewDuringLoginApi())
check('a renewal of A answered after B logged in is dropped', auth.refresh() is True and settings.token() == 'simkl_at_B' and settings.get(settings.REFRESH_TOKEN) == 'rB')

# --- invalidating A's rejected token while B's login is being published must not erase B ---
ident(token='simkl_at_A', refresh_token='rA', account_id='A')
auth = Auth(FakeApi())
auth._identity.acquire()
worker = threading.Thread(target=auth.invalidate, args=('simkl_at_A',))
worker.start()
worker.join(0.3)
check('the invalidation waits for the publication', worker.is_alive() and settings.token() == 'simkl_at_A')
ident(token='simkl_at_B', refresh_token='rB', account_id='B')
auth._identity.release()
worker.join(3)
check('and then finds another token and clears nothing', not worker.is_alive() and settings.token() == 'simkl_at_B' and settings.account_id() == 'B')
ident(token='simkl_at_A', account_id='A')
auth.invalidate('simkl_at_A')
check('the still-stored rejected token is cleared', settings.token() == '' and settings.account_id() == '')


# --- the renewal snapshot is taken under the identity lock ---
class RecordingApi:
    def __init__(self):
        self.bodies = []

    def request(self, method, path, body=None, token=None, timeout=30, client_id=None):
        self.bodies.append(dict(body))
        return 200, {'access_token': 'simkl_at_new', 'refresh_token': body['refresh_token'], 'expires_in': 604800}


ident(token='simkl_at_A', refresh_token='rA', token_expires='9999999999', account_id='A')
api = RecordingApi()
auth = Auth(api)
auth._identity.acquire()
worker = threading.Thread(target=auth.refresh)
worker.start()
worker.join(0.3)
check('the renewal waits for the publication before reading its snapshot', worker.is_alive() and api.bodies == [])
ident(token='simkl_at_B', refresh_token='rB', account_id='B')
auth._identity.release()
worker.join(3)
check('and renews with B\'s refresh token, never A\'s', not worker.is_alive() and [b['refresh_token'] for b in api.bodies] == ['rB'] and settings.get(settings.REFRESH_TOKEN) == 'rB')

# a renewal for a token another thread already replaced sends nothing
ident(token='simkl_at_T2', refresh_token='rA', token_expires='9999999999', account_id='A')
api = RecordingApi()
check('no second renewal for an already replaced token', Auth(api).refresh(rejected='simkl_at_T1') is True and api.bodies == [])

# --- a 2xx without JSON is a retryable error, a literal null is an empty answer ---
import urllib.request  # noqa: E402
from resources.lib import api as apimod  # noqa: E402

apimod.POST_GAP = 0.0   # the checks post back to back; the spacing itself is checked on its own


class FakeResponse:
    def __init__(self, status, body):
        self.status, self.body = status, body

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def getcode(self):
        return self.status

    def read(self, size=-1):
        body, self.body = self.body, b''
        return body


def answers(status, body):
    urllib.request.urlopen = lambda req, timeout=30: FakeResponse(status, body)
    return apimod.Api().request('POST', '/sync/history', {'movies': []}, token='simkl_at_A')


real_urlopen = urllib.request.urlopen
for status, body, expect in ((200, b'<html>Bad gateway</html>', 'error'), (200, b'', 'error'), (201, b'{"added": {}}', (201, {'added': {}})),
                             (200, b'null', (200, None)), (204, b'', (204, None))):
    try:
        got = answers(status, body)
        ok = expect != 'error' and got == expect
    except ApiError as exc:
        got = 'ApiError(%s, %s)' % (exc.status, exc.error)
        ok = expect == 'error' and exc.retryable
    check('HTTP %s body %r -> %s' % (status, body[:12], got), ok)
seen_urls = []


def capture(req, timeout=30):
    seen_urls.append(req.full_url)
    return FakeResponse(201, b'{"added": {}}')


urllib.request.urlopen = capture
apimod.Api().request('POST', '/sync/history', {'movies': []}, token='simkl_at_A')
apimod.Api().request('GET', '/sync/all-items?extended=full', token='legacyV1')
check('api: every URL carries app-name, app-version and the client id of the token generation',
      all('app-name=script.simkl' in u and 'app-version=4.0.0' in u for u in seen_urls)
      and seen_urls[0].endswith('&client_id=' + apimod.CLIENT_V2) and seen_urls[1].endswith('&client_id=' + apimod.CLIENT_LEGACY) and seen_urls[1].count('?') == 1)
sent_bodies = []
urllib.request.urlopen = lambda req, timeout=30: sent_bodies.append(req.data) or FakeResponse(201, b'{"type": "movie"}')
apimod.Api().request('POST', '/search/file', {'file': 'davs://nas/Am%E9lie%20(2001).mkv', 'part': 1}, token='simkl_at_A')
urllib.request.urlopen = real_urlopen
check('api: a percent sign in a body goes as the JSON escape, so Simkl\'s URL-decoding of the body leaves it as it was',
      b'%' not in sent_bodies[0] and json.loads(urllib.parse.unquote_to_bytes(sent_bodies[0])) == {'file': 'davs://nas/Am%E9lie%20(2001).mkv', 'part': 1})


# --- a login cancelled while its requests were out must not publish ---
class FakeDialog:
    closed = False

    def close(self):
        self.closed = True


class LoginApi:
    def __init__(self, cancel_in_flight):
        self.cancel_in_flight = cancel_in_flight
        self.dialog = None
        self.paths = []

    def request(self, method, path, body=None, token=None, timeout=30, client_id=None):
        self.paths.append(path)
        if self.cancel_in_flight:
            self.dialog.closed = True
            ident(token='simkl_at_B', refresh_token='rB', account_id='B')
        return 200, {'access_token': 'simkl_at_A', 'refresh_token': 'rA', 'expires_in': 604800}

    def call(self, method, path, body=None, token=None, timeout=30):
        return {'user': {'name': 'a'}, 'account': {'id': 12345, 'type': 'free'}}


for cancelled in (True, False):
    # the uncancelled case is a 3.x login reconnecting: the same account, already stored as a string
    ident(token='' if cancelled else 'legacyV1', refresh_token='', account_id='' if cancelled else '12345')
    api = LoginApi(cancelled)
    api.dialog = FakeDialog()
    auth = Auth(api)
    done = []
    writes = []
    real_write = settings._write_identity
    settings._write_identity = lambda state: writes.append(dict(state)) or real_write(state)
    auth._poll(api.dialog, 'device', 5, 900, lambda previous, current: done.append((previous, current)))
    settings._write_identity = real_write
    if cancelled:
        check('a cancelled login does not publish over the newer one', settings.token() == 'simkl_at_B' and settings.account_id() == 'B' and done == [])
    else:
        check('an uncancelled login publishes and reports (previous, current) as strings, a same-account reconnect compares equal',
              settings.token() == 'simkl_at_A' and settings.account_id() == '12345' and done == [('12345', '12345')])
        check('tokens and account reach the disk in one write, never a token under the previous account',
              len(writes) == 1 and writes[0]['token'] == 'simkl_at_A' and writes[0]['account_id'] == '12345')
ident(token='legacyV1', refresh_token='', account_id='777', account_name='Old')
api = LoginApi(False)
api.dialog = FakeDialog()
done = []
Auth(api)._poll(api.dialog, 'device', 5, 900, lambda previous, current: done.append((previous, current)))
check('reconnect: a device login to another account is refused, the old login stays and the new grant is revoked',
      settings.token() == 'legacyV1' and settings.account_id() == '777' and done == [] and '/oauth2/revoke' in api.paths)
ident(token='simkl_at_A', refresh_token='rA', account_id='A', account_name='')


# --- a truncated error body is still a normal HTTP error ---
import http.client  # noqa: E402
import urllib.error  # noqa: E402


class TruncatedError(urllib.error.HTTPError):
    def __init__(self):
        urllib.error.HTTPError.__init__(self, 'https://api.simkl.com/sync/history', 400, 'Bad Request', {}, None)

    def read(self):
        raise http.client.IncompleteRead(b'<ht')


def raise_truncated(req, timeout=30):
    raise TruncatedError()


urllib.request.urlopen = raise_truncated
try:
    apimod.Api().call('POST', '/sync/history', {'movies': []}, token='simkl_at_A')
    check('a 400 whose body was cut off -> ApiError', False)
except ApiError as exc:
    # a 400 may be RATE_LIMIT: without the body it is a transport failure to retry, never the final refusal that drops a queued watch
    check('a 400 whose body was cut off -> transport ApiError, retried', exc.transport and exc.retryable)
except Exception as exc:  # noqa: BLE001
    check('a 400 whose body was cut off -> transport ApiError, got %r' % exc, False)
urllib.request.urlopen = real_urlopen


# --- a sample of the next file must not overwrite the finished session's position ---
player = FakePlayer(2500.0, 2700.0, 'A')
scrobbler = scr.Scrobbler(api=None, auth=RefusingAuth(), player=player, sender=FakeSender(), history=fresh_queue(), marks=FakeMarks())
session = scr.Session(4)
session.item = dict(ITEM)
session.started = True
session.path = 'A'
session.position, session.total = 2500.0, 2700.0
scrobbler.session = session
for position, total, path, expect, label in ((0.0, 0.0, 'A', None, 'no duration during the switch'), (3.0, 1300.0, 'B', None, 'another duration (the next file)'),
                                             (2510.0, 2700.0, 'B', None, 'another file under the same duration'), (2510.0, 2700.0, 'A', (2510.0, 2700.0), 'own file, own duration')):
    player.position, player.total, player.path = position, total, path
    check('sample %s -> %s' % (label, 'kept' if expect else 'dropped'), scrobbler._sample(session) == expect)
check('the closed session keeps its last good position', round(scr.Scrobbler._progress(session), 1) == 92.6)

# --- a natural end is 100, a stream that ran out early keeps its position ---
session.position = 2698.5
check('natural end -> 100', scr.Scrobbler._end_progress(session) == 100.0)
session.position = 540.0
check('an early end keeps its real progress', scr.Scrobbler._end_progress(session) == 20.0)

# --- trailers, clips and a pause before the duration is known ---
sender = FakeSender()
scrobbler = scr.Scrobbler(api=None, auth=RefusingAuth(), player=FakePlayer(), sender=sender, history=fresh_queue(), marks=FakeMarks())
session = scr.Session(5)
session.item = dict(ITEM, runtime=7200)
session.total = 120.0
scrobbler._maybe_start(session)
check('start: nothing is decided in the first two seconds (Kodi\'s silent resume seek)', not session.ignored and not session.started)
session.created -= 5
scrobbler._maybe_start(session)
check('a 2-minute playback under a 2-hour movie is a trailer, not tracked', session.ignored and not session.started and sender.jobs == [])
session = scr.Session(6)
session.item = dict(ITEM, runtime=7200)
session.total = 7000.0
session.paused = True
session.created -= 5
scrobbler._maybe_start(session)
check('paused before the duration was known: started, start sent by the resume', session.started and sender.jobs == [])
EPISODE = {'kind': 'episode', 'key': 'episode:1', 'dbid': 2, 'showtitle': 'S', 'season': 1, 'number': 2, 'ids': {'tvdb': '1'}, 'label': 'S 1x02', 'runtime': 2700}
session = scr.Session(7)
session.item = dict(EPISODE)
session.total = 1200.0
session.created -= 5
scrobbler._maybe_start(session)
check('streams: a 20-minute episode under a show listed at 45 minutes is tracked (the clip rule is for movies)', session.started and not session.ignored)

# --- streams: no duration from Kodi -> the item's runtime stands in; none at all -> not tracked ---
sender = FakeSender()
player = FakePlayer(600.0, 0.0, 'A')
scrobbler = scr.Scrobbler(api=None, auth=RefusingAuth(), player=player, sender=sender, history=fresh_queue(), marks=FakeMarks())
session = scr.Session(8)
session.item = dict(EPISODE)
session.path = 'A'
session.created = time.time() - 30
scrobbler._maybe_start(session)
check('streams: after the wait the runtime stands in and the start goes out', session.started and session.assumed and session.total == 2700.0 and len(sender.jobs) == 1)
check('streams: samples without a duration keep the runtime as the total', scrobbler._sample(session) == (600.0, 2700.0) and session.assumed)
player.position, player.total = 700.0, 2650.0
check('streams: a duration Kodi learns later replaces the runtime', scrobbler._sample(session) == (700.0, 2650.0) and not session.assumed)
session = scr.Session(9)
session.item = dict(EPISODE, runtime=0)
session.created = time.time() - 30
scrobbler._maybe_start(session)
check('streams: no duration and no runtime -> not tracked', session.ignored and not session.started)
session = scr.Session(10)
session.item = dict(EPISODE)
session.created = time.time() - 5
scrobbler._maybe_start(session)
check('streams: inside the wait nothing is decided yet', not session.ignored and not session.started and session.total == 0.0)

# --- network shares are files, credentials never leave the box ---
check('smb/nfs paths are files for the file search', playerlib.is_local('smb://nas/Movies/x.mkv') and playerlib.is_local('nfs://nas/x.mkv') and playerlib.is_local(r'C:\x.mkv'))
check('streams are not', not playerlib.is_local('http://x/y.mkv') and not playerlib.is_local('plugin://plugin.video.x/?id=1') and not playerlib.is_local('pvr://channels/1'))
check('credentials stripped from a share path', playerlib.sanitized('smb://user:pass@nas/Movies/x.mkv') == 'smb://nas/Movies/x.mkv' and playerlib.sanitized(r'D:\x.mkv') == r'D:\x.mkv')

# --- an unknown season is Simkl's default, never a special; show ids of an add-on episode come from the property ---
check('season -1 -> 1', playerlib.remote_episode({'tvdb': '1'}, 'S', 'E', None, -1, 5)['season'] == 1 and playerlib.remote_episode({'tvdb': '1'}, 'S', 'E', None, 0, 5)['season'] == 0)
RPC['Player.GetActivePlayers'] = [{'playerid': 1, 'type': 'video'}]
RPC['Player.GetItem'] = {'item': {'type': 'episode', 'uniqueid': {'tvdb': '999', 'tmdb': '555'}, 'season': 2, 'episode': 3, 'showtitle': 'S', 'title': 'E', 'file': 'plugin://plugin.video.x/play'}}
WINDOW.clear()
status, payload = playerlib.identify(FakePlayer(path='plugin://plugin.video.x/play'))
check('an add-on episode without show ids is skipped, its own uniqueid is not taken for the show', status == 'skip' and 'show ids' in payload)
WINDOW['script.simkl.ids'] = json.dumps({'tvdb': '123'})
status, payload = playerlib.identify(FakePlayer(path='plugin://plugin.video.x/play'))
check('with the property the show ids come from it and the episode keeps its own tvdb id', status == 'ok' and payload['ids'] == {'tvdb': '123'} and payload['episode_ids'] == {'tvdb': '999'} and payload['season'] == 2)
RPC['Player.GetItem'] = {'item': {'type': 'movie', 'uniqueid': {'tmdb': '555'}, 'title': 'X (Trailer)', 'file': 'https://youtu.be/x'}}
status, payload = playerlib.identify(FakePlayer(path='https://youtu.be/x'))
check('Kodi\'s Play trailer item is skipped', status == 'skip' and payload == 'trailer')
WINDOW.clear()

# --- other add-ons: "I scrobble to Simkl myself" and "keep this item out", the Trakt-style conventions ---
RPC['Player.GetItem'] = {'item': {'type': 'movie', 'uniqueid': {'tmdb': '555'}, 'title': 'X', 'file': 'plugin://plugin.video.x/play'}}
WINDOW['script.simkl.paused'] = 'true'
status, payload = playerlib.identify(FakePlayer(path='plugin://plugin.video.x/play'))
check('coexistence: script.simkl.paused keeps this add-on out of the playback', status == 'skip' and 'scrobbles to Simkl itself' in payload)
WINDOW.clear()
WINDOW['script.trakt.paused'] = 'true'
status, payload = playerlib.identify(FakePlayer(path='plugin://plugin.video.x/play'))
check('coexistence: script.trakt.paused alone (Trakt scrobbling) does not', status == 'ok')
WINDOW.clear()
RPC['Player.GetItem'] = {'item': {'type': 'movie', 'uniqueid': {'tmdb': '555'}, 'title': 'X', 'file': 'plugin://plugin.video.x/play', 'customproperties': {'script.trakt.exclude': 'true'}}}
status, payload = playerlib.identify(FakePlayer(path='plugin://plugin.video.x/play'))
check('coexistence: a ListItem marked script.trakt.exclude / script.simkl.exclude is skipped', status == 'skip' and 'excluded by the add-on' in payload)
WINDOW['script.simkl.ids'] = '{"tmdb": "1"}'
WINDOW['script.simkl.paused'] = 'true'
playerlib.clear_ids()
check('coexistence: both Simkl properties are cleared at the end of a playback', 'script.simkl.ids' not in WINDOW and 'script.simkl.paused' not in WINDOW)

# --- developer options: per-item ids, flat ids, id-name normalisation, what the add-on announces ---
import base64  # noqa: E402

check('dev: id names normalised (case, _id suffix, numbers)', utils.ids_from({'TMDB': 12, 'imdb_id': 'tt9', 'trakt': 5, 'slug': 'x'}) == {'tmdb': '12', 'imdb': 'tt9'})
WINDOW.clear()
RPC['Player.GetItem'] = {'item': {'type': 'episode', 'uniqueid': {'tvdb': '999'}, 'season': 1, 'episode': 4, 'showtitle': 'S', 'file': 'plugin://x/play',
                                  'customproperties': {'script.simkl.ids': '{"tvdb": "321"}'}}}
status, payload = playerlib.identify(FakePlayer(path='plugin://x/play'))
check('dev: script.simkl.ids as a ListItem property names the show (no window property needed)', status == 'ok' and payload['ids'] == {'tvdb': '321'} and payload['episode_ids'] == {'tvdb': '999'})
RPC['Player.GetItem'] = {'item': {'type': 'episode', 'season': 1, 'episode': 4, 'showtitle': 'S', 'file': 'plugin://x/play',
                                  'customproperties': {'tvshow.tmdb_id': '777', 'tvshow.imdb_id': 'tt77', 'tmdb_id': '55'}}}
status, payload = playerlib.identify(FakePlayer(path='plugin://x/play'))
check('dev: flat tvshow.* properties name the show, the item\'s own tmdb_id is not taken for it', status == 'ok' and payload['ids'] == {'tmdb': '777', 'imdb': 'tt77'} and payload['episode_ids'] == {})
RPC['Player.GetItem'] = {'item': {'type': 'movie', 'title': 'M', 'year': 2020, 'file': 'plugin://x/play', 'customproperties': {'tmdb_id': '55', 'tvshow.tmdb_id': '777'}}}
status, payload = playerlib.identify(FakePlayer(path='plugin://x/play'))
check('dev: flat tmdb_id names a movie, the tvshow.* one is not taken for it', status == 'ok' and payload['kind'] == 'movie' and payload['ids'] == {'tmdb': '55'})
RPC['Player.GetItem'] = {'item': {'type': 'movie', 'id': 7, 'file': 'plugin://x/play', 'customproperties': {'script.simkl.ids': '{"imdb": "tt7"}'}}}
RPC['VideoLibrary.GetMovieDetails'] = {'moviedetails': {'movieid': 7, 'title': 'Lib', 'year': 2001, 'uniqueid': {}, 'file': r'D:\lib\m.mkv', 'runtime': 5400}}
status, payload = playerlib.identify(FakePlayer(path='plugin://x/play'))
check('dev: a library item without ids takes the ids an add-on provides', status == 'ok' and payload['dbid'] == 7 and payload['ids'] == {'imdb': 'tt7'})
RPC.pop('VideoLibrary.GetMovieDetails')
BUILTINS.clear()
playerlib.set_playing(dict(ITEM))
playerlib.announce('scrobbled', dict(ITEM))
told = json.loads(WINDOW.get('script.simkl.playing') or '{}')
sent = BUILTINS[-1] if BUILTINS else ''
check('dev: script.simkl.playing names the tracked item', told.get('kind') == 'movie' and told.get('ids') == {'imdb': 'tt1'} and told.get('title') == 'X')
check('dev: the scrobbled announcement carries the same object, base64-wrapped for NotifyAll',
      sent.startswith('NotifyAll(script.simkl, scrobbled, {"data": "') and json.loads(base64.b64decode(sent.split('"data": "')[1].rstrip('"})')))['ids'] == {'imdb': 'tt1'})
playerlib.clear_ids()
check('dev: the playing property is cleared with the playback', 'script.simkl.playing' not in WINDOW)

# --- Up Next moving on counts as a natural end ---
sender = FakeSender()
scrobbler = scr.Scrobbler(api=None, auth=RefusingAuth(), player=FakePlayer(2000.0, 2700.0, 'A'), sender=sender, history=fresh_queue(), marks=FakeMarks())
session = scr.Session(9)
session.item = dict(ITEM)
session.started = True
session.path = 'A'
session.position, session.total = 2000.0, 2700.0
scrobbler.session = session
scrobbler.on_event(('upnext', 9))
check('dev: NEXTUPWATCHEDSIGNAL closes the session as watched to the end', session.closed and session.stopped_at is not None and kinds(scrobbler.history) == [h.WATCH])
session = scr.Session(10)
session.item = dict(EPISODE)
session.started = True
session.path = 'A'
session.position, session.total = 20.0, 2700.0
scrobbler.session = session
scrobbler.on_event(('upnext', 10))
early = not session.closed and kinds(scrobbler.history) == [h.WATCH]
session.started = False
session.position = 2600.0
scrobbler.on_event(('upnext', 10))
check('dev: an Up Next signal for a playback that has just begun (some versions send it when their popup closes, after the next episode started) or not started yet is ignored',
      early and not session.closed and kinds(scrobbler.history) == [h.WATCH])

# --- the window ids of a playback are cleared at its end only while they still hold its values ---
WINDOW.clear()
WINDOW['script.simkl.paused'] = 'true'
playerlib.clear_ids({'script.simkl.paused': ''})
kept_flag = WINDOW.get('script.simkl.paused') == 'true'
playerlib.clear_ids({'script.simkl.paused': 'true'})
check('window ids: a snapshot clears a property only while it still holds the snapshot\'s value', kept_flag and 'script.simkl.paused' not in WINDOW)
RPC['Player.GetActivePlayers'] = [{'playerid': 1, 'type': 'video'}]
RPC['Player.GetItem'] = {'item': {'type': 'episode', 'uniqueid': {'tvdb': '999'}, 'season': 1, 'episode': 1, 'showtitle': 'S', 'title': 'E', 'file': 'plugin://plugin.video.x/play'}}
auth_logged = types.SimpleNamespace(logged_in=lambda: True, authorized=lambda call, account=None: call('t'), nudge=lambda: None)
scrobbler = scr.Scrobbler(api=None, auth=auth_logged, player=FakePlayer(path='plugin://plugin.video.x/play'), sender=FakeSender(), history=fresh_queue(), marks=FakeMarks())
WINDOW['script.simkl.ids'] = '{"tvdb": "123"}'
scrobbler.on_event(('av_started', 1))
first = scrobbler.session.item['ids'] == {'tvdb': '123'} and scrobbler.session.window['script.simkl.ids'] == '{"tvdb": "123"}'
scrobbler.on_event(('av_started', 2))
check('window ids: an in-place switch to an item whose add-on set nothing clears the previous item\'s ids, so it is not taken for that one',
      first and 'script.simkl.ids' not in WINDOW and scrobbler.session.ignored and scrobbler.session.item is None)
WINDOW['script.simkl.ids'] = '{"tvdb": "123"}'
scrobbler.on_event(('av_started', 3))
WINDOW['script.simkl.ids'] = '{"tvdb": "456"}'
scrobbler.on_event(('ended', 3))
kept = WINDOW.get('script.simkl.ids') == '{"tvdb": "456"}'
scrobbler.on_event(('av_started', 4))
check('window ids: what the next item\'s add-on wrote before Kodi announced the end of the previous one stays and names the next item',
      kept and scrobbler.session.item['ids'] == {'tvdb': '456'})
WINDOW['script.simkl.playing'] = '{}'
scrobbler.on_event(('stopped', 4))
check('window ids: a value still the ended item\'s own is cleared, the playing property always', 'script.simkl.ids' not in WINDOW and 'script.simkl.playing' not in WINDOW)
WINDOW['script.simkl.ids'] = '{"tvdb": "789"}'
scrobbler.on_event(('av_started', 5))
scrobbler.on_event(('ended', 4))
late = WINDOW.get('script.simkl.ids') == '{"tvdb": "789"}'
scrobbler.session = None
scrobbler.on_event(('ended', 5))
check('window ids: an end announced for an earlier playback leaves the current item\'s values; without a session everything goes', late and 'script.simkl.ids' not in WINDOW)
WINDOW.clear()
RPC.pop('Player.GetItem')
RPC.pop('Player.GetActivePlayers')

# --- the Simkl log filter of the Advanced page (default.py) ---
import importlib  # noqa: E402

sys.argv = ['default.py']
default_script = importlib.import_module('resources.lib.script')
SAMPLE = [
    '2026-10-27 16:45:40.100 T:1    debug <general>: CPlayerCoreFactory::GetPlayers(x.mkv)',
    '2026-10-27 16:45:40.584 T:11620    info <general>: [script.simkl] stop 93% -> scrobble',
    '2026-10-27 16:45:41.000 T:2    error <general>: EXCEPTION Thrown (PythonToCppException) : -->Python callback/script returned the following error<--',
    '                                                   Traceback (most recent call last):',
    '                                                     File "C:\\Kodi\\addons\\script.simkl\\service.py", line 1, in <module>',
    '                                                   ValueError: boom',
    '2026-10-27 16:45:42.000 T:3    debug <general>: unrelated line',
]
raw, compact = default_script.simkl_lines(SAMPLE)
check('log: Simkl lines and the whole error block naming the add-on are kept, the rest dropped',
      raw == SAMPLE[1:6] and compact[0] == '16:45:40 info: stop 93% -> scrobble' and compact[1].startswith('16:45:41 error: EXCEPTION') and compact[2] == '  Traceback (most recent call last):')
check('log: the tail is bounded', len(default_script.simkl_lines(SAMPLE * 1000, keep=50)[0]) == 50)

# --- the login QR code (module-for-module agreement with the reference library is in qr_reference.py) ---
from resources.lib import qr  # noqa: E402

code = qr.matrix('https://simkl.com/pin?user_code=AB12-CD34')
check('qr: the pin link fits a version-3 code with its three finder patterns', len(code) == 29 and code[0][0] and code[0][28] and code[28][0] and not code[7][7])
check('qr: the PNG starts with the signature', qr.png('x')[:8] == b'\x89PNG\r\n\x1a\n')

# --- focused list item from info labels (RunScript from a skin or a keymap) ---
from resources.lib import focus  # noqa: E402

LABELS.update({'ListItem.DBTYPE': 'episode', 'ListItem.DBID': '12', 'ListItem.UniqueID(tvdb)': '4', 'ListItem.Season': '2', 'ListItem.Episode': '3', 'ListItem.TVShowTitle': 'S', 'ListItem.Label': 'E'})
payload = focus.from_infolabels()
check('dev: RunScript payload from the ListItem info labels', payload['dbtype'] == 'episode' and payload['dbid'] == 12 and payload['ids'] == {'tvdb': '4'} and payload['season'] == 2 and payload['number'] == 3)
LABELS.clear()
LABELS['System.BuildVersion'] = '21.2'
picked = []
for PICK[0] in (0, 9, 10, -1):
    picked.append(focus.pick_rating(_Addon(), 'X'))
check('rate: the picker maps 10..1, then Remove = 0, cancel = None', picked == [10, 1, 0, None])
PICK[0] = 0
check('rate: the prompt after a movie opens on "Not now", so a stray OK changes nothing', focus.pick_rating(_Addon(), 'X', 0, skip=True) is None and PRESELECT[0] == 0)
PICK[0] = 1
check('rate: behind "Not now", 10 is the second entry', focus.pick_rating(_Addon(), 'X', 0, skip=True) == 10)
PICK[0] = 11
check('rate: and Remove the last', focus.pick_rating(_Addon(), 'X', 0, skip=True) == 0)
PICK[0] = -1
focus.pick_rating(_Addon(), 'X', 7)
first = PRESELECT[0]
focus.pick_rating(_Addon(), 'X', 7, skip=True)
check('rate: the current rating is preselected (7: the fourth entry, the fifth behind "Not now")', first == 3 and PRESELECT[0] == 4)
check('rate: the subject of an episode row is its show, with no preselection; a movie row is itself with its rating',
      focus.rating_subject({'dbtype': 'episode', 'showtitle': 'S', 'label': 'S 1x02', 'userrating': 8}) == ('S', 0) and focus.rating_subject({'dbtype': 'movie', 'label': 'M', 'userrating': 8}) == ('M', 8))
check('rate: a library movie item carries its Kodi rating for the prompt', playerlib.movie_item({'movieid': 1, 'userrating': '7'})['userrating'] == 7)

# --- _deliver posts, falls back to the file search on a 404 and posts the resolved payload once ---
class DeliverApi:
    def __init__(self, answers):
        self.answers = list(answers)
        self.posts = []

    def call(self, method, path, body=None, token=None, timeout=30):
        if path.startswith('/search/file'):
            return {'type': 'movie', 'movie': {'title': 'X', 'year': 2020, 'ids': {'simkl': 5}}}
        self.posts.append((path, body))
        answer = self.answers.pop(0)
        if isinstance(answer, ApiError):
            raise answer
        return answer


ident(token='simkl_at_A', account_id='A')
for answers, expect, label in (([ApiError(404, 'not_found'), {'action': 'start'}], {'action': 'start'}, '404 -> file search -> resolved payload'),
                               ([ApiError(409, 'conflict')], None, '409 -> None, no search'),
                               ([ApiError(404, 'not_found'), ApiError(404, 'not_found')], None, 'resolved item unknown too -> unmatched')):
    api = DeliverApi(answers)
    passing = types.SimpleNamespace(logged_in=lambda: True, authorized=lambda call, account=None: call('simkl_at_A'))
    scrobbler = scr.Scrobbler(api=api, auth=passing, player=FakePlayer(), sender=FakeSender(), history=fresh_queue(), marks=FakeMarks())
    session = scr.Session(10)
    session.item = dict(ITEM, file=r'D:\x.mkv')
    playerlib._searches = {}
    got = scrobbler._deliver(session, '/scrobble/start?min_progress=70', 10.0)
    posted = [body for path, body in api.posts]
    ok = got == expect and all(body['progress'] == 10.0 for body in posted) and posted[0]['movie']['ids'] == {'imdb': 'tt1'}
    if len(posted) == 2:
        ok = ok and posted[1]['movie']['ids'] == {'simkl': '5'} and session.resolved['movie']['ids'] == {'simkl': '5'}
    if expect is None and len(answers) == 2:
        ok = ok and session.unmatched
    if expect is None and len(answers) == 1:
        ok = ok and len(posted) == 1 and not session.unmatched
    check('_deliver %s' % label, ok)

# --- files outside the library: search_file names them (context menu marks) by the full path, one request ---
class SearchApi:
    def __init__(self, known):
        self.known = known
        self.asked = []

    def call(self, method, path, body=None, token=None, timeout=30):
        self.asked.append(body['file'])
        if body['file'] in self.known:
            return {'type': 'episode', 'show': {'title': 'Gnosia', 'ids': {'simkl': 9}}, 'episode': {'season': 1, 'episode': 2}}
        raise ApiError(404, 'not_found')


playerlib._searches = {}
api = SearchApi({r'D:\TV\Gnosia\Gnosia - 02.mkv'})
passing = types.SimpleNamespace(authorized=lambda call, account=None: call('simkl_at_A'))
found = playerlib.search_file(api, passing, 'A', {'kind': None, 'dbid': None, 'file': r'D:\TV\Gnosia\Gnosia - 02.mkv', 'title': 'Gnosia - 02'})
check('files: a file outside the library is named by its full path, one request',
      api.asked == [r'D:\TV\Gnosia\Gnosia - 02.mkv'] and found['kind'] == 'episode' and found['season'] == 1 and found['number'] == 2 and found['ids'] == {'simkl': '9'})
check('files: an answer of another kind than the item is not taken', playerlib.search_file(SearchApi({r'D:\Alien.mkv'}), passing, 'A', {'kind': 'movie', 'dbid': None, 'file': r'D:\Alien.mkv'}) is None)
check('files: unknown to Simkl -> None', playerlib.search_file(SearchApi(set()), passing, 'A', {'kind': None, 'dbid': None, 'file': r'D:\yellow.mkv'}) is None)
# the bare name alone is never asked: Simkl answers 'S01E01.mkv' with whatever show the leftover word matches
for path in (r'smb://nas/TV/Qwzxv Unknownshow/Season 1/S01E01.mkv', r'D:\Movies\Unknown (2011)\VIDEO_TS\VIDEO_TS.IFO', r'D:\TV\Gnosia\Gnosia - 02.mkv', 'smb://nas/Сериалы/Во все тяжкие/Season 1/Во все тяжкие S01E01.mkv'):
    playerlib._searches = {}
    api = SearchApi({os.path.basename(path)})
    found = playerlib.search_file(api, passing, 'A', {'kind': None, 'dbid': None, 'file': path})
    check('files: the bare name is never asked, an answer Simkl would give for it never taken (%s)' % os.path.basename(path), found is None and api.asked == [path])
api = SearchApi(set())
playerlib.search_file(api, passing, 'A', {'kind': None, 'dbid': None, 'file': 'stack://smb://user:secret@nas/Movies/Alien-cd1.avi , smb://user:secret@nas/Movies/Alien-cd2.avi'})
check('files: a stacked item is searched by its first part, without the share credentials', api.asked == ['smb://nas/Movies/Alien-cd1.avi'])
api = SearchApi(set())
playerlib.search_file(api, passing, 'A', {'kind': None, 'dbid': None, 'file': 'rar://smb%3a%2f%2fuser%3asecret%40nas%2fMovies%2fAlien.rar/Alien.mkv'})
check('files: an archived item is searched by its archive, without the share credentials', api.asked == ['smb://nas/Movies/Alien.rar'])

# --- the search's answers are kept per path: a miss for 3 days, a hit for 7, across restarts; a failure is not ---
playerlib._searches = {}
api = SearchApi(set())
unknown = {'kind': None, 'dbid': None, 'file': r'D:\zorro.mkv'}
playerlib.search_file(api, passing, 'A', unknown)
first = list(api.asked)
again = playerlib.search_file(api, passing, 'A', unknown)
for entry in playerlib._searches.values():
    entry[0] -= playerlib.SEARCH_MISS_SECONDS + 1
playerlib.search_file(api, passing, 'A', unknown)
check('files: a file Simkl does not know is asked once, not again within 3 days, again after', first == [r'D:\zorro.mkv'] and again is None and api.asked == first * 2)
api = SearchApi({r'D:\TV\Gnosia\Gnosia - 02.mkv'})
known = {'kind': None, 'dbid': None, 'file': r'D:\TV\Gnosia\Gnosia - 02.mkv', 'title': 'Gnosia - 02'}
playerlib.search_file(api, passing, 'A', known)
asked_once = list(api.asked)
again = playerlib.search_file(api, passing, 'A', known)
check('files: a known file is not asked again, the kept answer names it', api.asked == asked_once and again['ids'] == {'simkl': '9'} and again['number'] == 2 and again['file'] == known['file'])
playerlib._searches = None
api = SearchApi(set())
again = playerlib.search_file(api, passing, 'A', known)
check('files: the answers come back from search.json after a restart', api.asked == [] and again is not None and again['ids'] == {'simkl': '9'})
for entry in playerlib._searches.values():
    entry[0] -= playerlib.SEARCH_HIT_SECONDS + 1
api = SearchApi({r'D:\TV\Gnosia\Gnosia - 02.mkv'})
playerlib.search_file(api, passing, 'A', known)
check('files: a kept answer is asked again after 7 days', playerlib.SEARCH_HIT_SECONDS == 7 * 86400 and api.asked == asked_once)


class FailingSearchApi:
    def call(self, method, path, body=None, token=None, timeout=30):
        raise ApiError(0, 'transport')


try:
    playerlib.search_file(FailingSearchApi(), passing, 'A', {'kind': None, 'dbid': None, 'file': r'D:\wolf.mkv'})
except ApiError:
    pass
check('files: a failed search keeps nothing', r'D:\wolf.mkv' not in playerlib._searches)
now = time.time()
playerlib._searches = {'f%d' % n: [now - n, {}] for n in range(playerlib.SEARCH_MAX)}
playerlib.search_file(SearchApi(set()), passing, 'A', {'kind': None, 'dbid': None, 'file': r'D:\new.mkv'})
check('files: the cache keeps %d names, the oldest go' % playerlib.SEARCH_MAX,
      len(playerlib._searches) == playerlib.SEARCH_MAX and r'D:\new.mkv' in playerlib._searches and 'f999' not in playerlib._searches and 'f0' in playerlib._searches)
playerlib._searches = {}


class SeriesApi:
    """The search places the file in a series but at no episode; the title's record says whether it is an anime movie."""
    def __init__(self, anime_type):
        self.anime_type = anime_type
        self.asked = []

    def call(self, method, path, body=None, token=None, timeout=30):
        self.asked.append(path)
        if path.startswith('/search/file'):
            return {'type': 'show', 'show': {'title': 'T', 'year': 2020, 'ids': {'simkl': 7}}}
        answer = {'type': 'anime' if self.anime_type else 'show'}
        if self.anime_type:
            answer['anime_type'] = self.anime_type
        return answer


for anime_type, expect in (('movie', 'movie'), ('tv', None), (None, None)):
    api = SeriesApi(anime_type)
    playerlib._searches = {}
    found = playerlib.search_file(api, passing, 'A', {'kind': None, 'dbid': None, 'file': r'D:\Akira.mkv', 'title': 'Akira'})
    check('files: a show answered without an episode is taken only as an anime movie (anime_type %s)' % anime_type,
          (found['kind'] if found else None) == expect and api.asked == ['/search/file', '/anime/7'])

# --- a 409 "already watched within the hour" on a stop: the write-ahead watch is acked, nothing queued ---
class ConflictAuth:
    def logged_in(self):
        return True

    def authorized(self, call, account=None):
        raise ApiError(409, '', '', {'watched_at': '2026-10-27T01:18:11Z', 'expires_at': '2026-10-27T02:18:11Z'})


q = fresh_queue()
scrobbler = scr.Scrobbler(api=None, auth=ConflictAuth(), player=FakePlayer(), sender=FakeSender(), history=q, marks=FakeMarks())
session = scr.Session(11)
session.item = dict(ITEM)
session.started = True
session.stopped_at = time.time()
eid = q.watch_event(dict(ITEM), session.stopped_at, False)
scrobbler._send(session, 'stop', 96.0, eid)
check('conflict: a 409 on the stop drops the write-ahead watch (Simkl has it)', q.entries == [])

# --- the file search's 'show' answer for an anime movie ---
check('a show answered without an episode is shaped as a movie by id', playerlib._resolved_from({'type': 'show', 'show': {'title': 'Anime Movie', 'year': 2019, 'ids': {'simkl': 5}}}, {'file': 'x', 'title': 'x'})['kind'] == 'movie')
api = DeliverApi([ApiError(404, 'not_found')])
playerlib._searches = {}
scrobbler = scr.Scrobbler(api=api, auth=passing, player=FakePlayer(), sender=FakeSender(), history=fresh_queue(), marks=FakeMarks())
session = scr.Session(12)
session.item = dict(ITEM, file=r'D:\x.mkv')
session.resolved = {'movie': {'ids': {'simkl': '5'}}}
check('Simkl refusing what the file search named ends the playback\'s tracking (no write-ahead watch later)', scrobbler._deliver(session, '/scrobble/start?min_progress=70', 10.0) is None and session.unmatched)

# --- the library unmark window ---
q = fresh_queue()
queued_watch(q, time.time() - 10)
item2 = dict(ITEM, key='movie:2', dbid=2)
q.add_unmark(ITEM)
q.add_unmark(item2)
check('library unmarks wait in the window and touch nothing yet', kinds(q) == [h.WATCH, h.UNMARK, h.UNMARK] and q._eligible() == [q.entries[0]])
q._settle_window(q.window_last + 5)
check('a bulk unmark is dropped and the queued watch stays', kinds(q) == [h.WATCH])
q = fresh_queue()
queued_watch(q, time.time() - 10)
q.add_unmark(ITEM)
q._settle_window(q.window_last + 5)
asked_single = len([e for e in q.entries if e.get('asking')])
answer_all(q)
check('a single unmark supersedes the older watch, is asked about (a movie) and goes out', asked_single == 1 and kinds(q) == [h.UNMARK] and 'window' not in q.entries[0] and q._eligible() == q.entries)
q = fresh_queue()
q.add_unmark(ITEM)
q.add_unmark(item2)
q.entries[0]['window'] -= 1.5
q._settle_window(q.window_last + 5)
answer_all(q)
check('two unmarks made one by one (more than a second apart) both go out', kinds(q) == [h.UNMARK, h.UNMARK] and all('window' not in e for e in q.entries) and q._eligible() == q.entries)
q = fresh_queue()
q.add_unmark(ITEM)
q.note_folder_unmark()
q._settle_window(q.window_last + 5)
check('a folder\'s own announcement makes even a single unmark a bulk one', q.entries == [])

# --- the cap evicts marks before watch events ---
q = fresh_queue()
real_cap = h.MAX_ENTRIES
h.MAX_ENTRIES = 3
queued_watch(q, 100.0)
for n in range(2, 6):
    q.add_mark(dict(ITEM, key='movie:%d' % n, dbid=n))
check('over the cap the oldest marks go, the watch event stays', len(q.entries) == 3 and kinds(q)[0] == h.WATCH and [e['key'] for e in q.entries][1:] == ['movie:4', 'movie:5'])
h.MAX_ENTRIES = real_cap

# --- an unreadable queue file is moved aside, persistence stays on ---
with open(store.path(store.QUEUE), 'w', encoding='utf-8') as handle:
    handle.write('{not json')
q = h.History(api=None, auth=None)
q.add_mark(ITEM)
check('a manual mark only flags the queue dirty, the worker writes it once per turn', q.dirty)
q.save_if_dirty()
check('corrupt queue.json quarantined, the new queue is written', os.path.exists(store.path(store.QUEUE) + '.corrupt') and json.load(open(store.path(store.QUEUE)))['entries'] and not q.dirty)
check('fast stamp parse agrees with strptime', utils.parse_stamp('2026-10-25T20:00:00Z') == 1792958400 and utils.parse_stamp('2026-13-25T20:00:00Z') is None
      and utils.parse_stamp('1970-01-01T00:00:01Z') is None and utils.parse_stamp('bad') is None and utils.parse_stamp('2026-10-25T20:00:00.000Z') is None)

# --- an unmarked movie: a question first (remove, Plan to watch or keep), then Simkl's lists decide what can be done ---
class RemoveApi:
    """Simkl's side of a movie unmark: the /sync/watched check (listed: in the completed list; status: the list it is
    in instead; rewatched: with sessions; bare: an older server's answer without the count; refuse: a coded refusal;
    short: an answer with a row missing), then the removal or the move."""
    def __init__(self, rewatched=False, listed=True, refuse=False, status='completed', bare=False, short=False):
        self.paths = []
        self.full = []
        self.bodies = []
        self.removed = []
        self.checked = []
        self.rewatched = rewatched
        self.listed = listed
        self.refuse = refuse
        self.status = status
        self.bare = bare
        self.short = short

    def call(self, method, path, body=None, token=None, timeout=30):
        self.paths.append(path.split('?')[0])
        self.full.append(path)
        if path.startswith('/sync/watched'):
            self.checked.append((method, body))
            if self.refuse:
                raise ApiError(400, 'invalid_request', 'bad body')
            rows = []
            for item in body[1:] if self.short else body:
                if not self.listed:
                    rows.append(dict(item, result=False))
                    continue
                row = dict(item, result=True, list=self.status, last_watched_at='2026-01-01T00:00:00Z')
                if not self.bare:
                    row['rewatches'] = 2 if self.rewatched else 0
                rows.append(row)
            return rows
        if path.startswith('/sync/add-to-list'):
            self.bodies.append(body)
            return {'added': {'movies': body['movies']}, 'not_found': {}}
        self.removed.append(body)
        return {'deleted': {'movies': 1}, 'not_found': {}}


class PassAuth:
    def logged_in(self):
        return True

    def authorized(self, call, account=None):
        return call('simkl_at_A')


WATCHED, LIST, REMOVE = '/sync/watched', '/sync/add-to-list', '/sync/history/remove'
PLAN_BODY = {'movies': [{'ids': {'imdb': 'tt1'}, 'title': 'X', 'year': 2020, 'to': 'plantowatch'}]}
REMOVE_BODY = {'movies': [{'ids': {'imdb': 'tt1'}, 'title': 'X', 'year': 2020}], 'shows': []}
for label, api, choice, expect_paths, toast in (
        ('plan: a completed movie is moved, its rewatch sessions stay, the toast comes with Simkl\'s answer', RemoveApi(rewatched=True), 'plan', [WATCHED, LIST], 'str32254 X (2020)'),
        ('plan: a completed movie is moved after the one watched check', RemoveApi(), 'plan', [WATCHED, LIST], 'str32254 X (2020)'),
        ('plan: a movie Simkl does not have as completed is dropped, an explicit unmark says so', RemoveApi(listed=False), 'plan', [WATCHED], 'str32310 X (2020)'),
        ('plan: a movie Simkl has in another list than completed is dropped the same way', RemoveApi(status='plantowatch'), 'plan', [WATCHED], 'str32310 X (2020)'),
        ('remove: a completed movie without rewatch sessions leaves the history, the toast comes with Simkl\'s answer', RemoveApi(), 'remove', [WATCHED, REMOVE], 'str32256 X (2020)'),
        ('remove: a movie with rewatch sessions is kept, with a toast', RemoveApi(rewatched=True), 'remove', [WATCHED], 'str32309 X (2020)'),
        ('remove: an answer without the session count (an older server) keeps the movie too', RemoveApi(bare=True), 'remove', [WATCHED], 'str32309 X (2020)'),
        ('remove: a movie Simkl does not have as completed is dropped', RemoveApi(listed=False), 'remove', [WATCHED], 'str32310 X (2020)'),
        ('keep: nothing is asked of Simkl, the unmark stays in the ledger', RemoveApi(), 'keep', [], None),
        ('refused: a coded refusal of the watched check keeps the unmark in Kodi only, without the "not in your history" toast', RemoveApi(refuse=True), 'remove', [WATCHED], None)):
    ident(token='simkl_at_A', account_id='A')
    asked = []
    toasts = []
    utils.notify = lambda message, *a, **k: toasts.append(message)
    q = h.History(api=api, auth=PassAuth(), ask=asked.append)
    q.entries, q.unmarked, q.window_unmarks = [], {}, []
    q.add_unmark(ITEM, explicit=True)
    sender = FakeSender()
    q.tick(time.time() + 5, sender)
    before = len(sender.jobs)
    q.answer_unmark(asked[0], choice)
    q.tick(time.time() + 5, sender)
    for job in sender.jobs:
        job()
    utils.notify = real_notify
    check('unmark question, ' + label,
          len(asked) == 1 and before == 0 and api.paths == expect_paths and q.entries == [] and not q.flushing and q.unmarked.get(ITEM['key'])
          and (not api.bodies or api.bodies == [PLAN_BODY]) and (not api.removed or api.removed == [REMOVE_BODY]) and toasts == ([toast] if toast else [])
          and all('allow_rewatch=yes' in p for p in api.full if p.startswith('/sync/watched'))
          and (not api.checked or api.checked == [('POST', [{'ids': {'imdb': 'tt1'}}])]))
ident(token='simkl_at_A', account_id='A')
asked = []
api = RemoveApi(short=True)
q = h.History(api=api, auth=PassAuth(), ask=asked.append)
q.entries, q.unmarked, q.window_unmarks = [], {}, []
q.add_unmark(ITEM, explicit=True)
q.add_unmark(dict(ITEM, key='movie:2', dbid=2, ids={'imdb': 'tt2'}, label='Y'), explicit=True)
answer_all(q, 'remove')
sender = FakeSender()
q.tick(time.time() + 5, sender)
for job in sender.jobs:
    job()
check('unmark question: a watched check answering fewer rows than movies asked is not trusted, both movies wait with a wait of their own',
      api.paths == [WATCHED] and api.removed == [] and sorted(e['item']['label'] for e in q.entries) == ['X (2020)', 'Y'] and all(e.get('retry_wait') == h.BACKOFF_MIN for e in q.entries))

ident(token='simkl_at_A', account_id='A')
asked = []
api = RemoveApi()
q = h.History(api=api, auth=PassAuth(), ask=asked.append)
q.entries, q.unmarked, q.window_unmarks = [], {}, []
q.add_unmark(EPISODE, explicit=True)
q.add_unmark(ITEM, explicit=True)
q.answer_unmark(asked[0], 'remove')
sender = FakeSender()
q.tick(time.time() + 5, sender)
sender.jobs[0]()
check('unmark question: an episode and a movie answered "remove" leave the history in one request, both remembered',
      api.paths == [WATCHED, REMOVE] and len(api.removed) == 1 and len(api.removed[0]['shows']) == 1 and len(api.removed[0]['movies']) == 1
      and q.entries == [] and q.unmarked.get(EPISODE['key']) and q.unmarked.get(ITEM['key']) and q.sent_unmarks.get(ITEM['key']) == q.unmarked[ITEM['key']])


class ServerErrorApi(RemoveApi):
    """The watched check fails with a server error; everything else answers as usual."""
    def call(self, method, path, body=None, token=None, timeout=30):
        if path.startswith('/sync/watched'):
            self.paths.append(path.split('?')[0])
            raise ApiError(500, 'server_error', 'down')
        return RemoveApi.call(self, method, path, body, token, timeout)


asked = []
api = ServerErrorApi()
q = h.History(api=api, auth=PassAuth(), ask=asked.append)
q.entries, q.unmarked, q.window_unmarks = [], {}, []
q.add_unmark(EPISODE, explicit=True)
q.add_unmark(ITEM, explicit=True)
q.answer_unmark(asked[0], 'remove')
sender = FakeSender()
q.tick(time.time() + 5, sender)
sender.jobs[0]()
movie = [e for e in q.entries if e['item']['kind'] == 'movie']
check('unmark question: a watched check that fails holds the movie back with a wait of its own, the episode leaves the history now',
      api.paths == [WATCHED, REMOVE] and len(api.removed) == 1 and api.removed[0]['movies'] == [] and len(api.removed[0]['shows']) == 1
      and [e['item']['kind'] for e in q.entries] == ['movie'] and movie[0]['retry_wait'] == h.BACKOFF_MIN and movie[0]['retry_at'] > time.time()
      and q.due == movie[0]['retry_at'] and EPISODE['key'] in q.unmarked and ITEM['key'] not in q.unmarked)
movie[0]['retry_at'] = 0
q.api = RemoveApi()
sender = FakeSender()
q.due = 0
q.tick(time.time() + 5, sender)
sender.jobs[0]()
check('unmark question: once the list answers, the held-back movie leaves the history', q.api.removed == [REMOVE_BODY] and q.entries == [] and ITEM['key'] in q.unmarked)
q5 = h.History(api=api, auth=PassAuth(), ask=asked.append)
q5.entries = [dict(q5._new(h.UNMARK, ITEM), explicit=True)]
q5._save()
q6 = h.History(api=api, auth=PassAuth(), ask=asked.append)
asked.clear()
q6.tick(time.time(), FakeSender())
check('unmark question: a question cut off by a restart is asked again at the first tick', q6.entries[0].get('asking') and asked == [q6.entries[0]['id']])

# --- a flush with plain marks and rewatch watches: two requests, the rewatch one with one show entry per episode ---
class HistoryApi:
    def __init__(self):
        self.sent = []

    def call(self, method, path, body=None, token=None, timeout=30):
        self.sent.append((path, body))
        return {'added': {'movies': 0, 'episodes': 1}, 'not_found': {}}


ident(token='simkl_at_A', account_id='A')
api = HistoryApi()
q = h.History(api=api, auth=PassAuth())
q.entries, q.unmarked, q.window_unmarks = [], {}, []
q.add_mark(ITEM)
for number in (3, 4):
    eid = q.watch_event(dict(EPISODE, key='episode:%d' % number, dbid=number, number=number), time.time() - 10, True)
    q.release(eid, toast=False)
sender = FakeSender()
q.due = 0
q.tick(time.time() + 5, sender)
sender.jobs[0]()
paths = [path for path, _ in api.sent]
rewatch_body = api.sent[-1][1] if api.sent else {}
check('flush: one plain request and one allow_rewatch request with a show entry per episode',
      paths == ['/sync/history', '/sync/history?allow_rewatch=yes'] and len(rewatch_body.get('shows') or []) == 2
      and [s['seasons'][0]['episodes'][0]['number'] for s in rewatch_body['shows']] == [3, 4] and q.entries == [])

# --- Kodi's own playcount bump during the playback is not a manual mark ---
marks = Marks(auth=types.SimpleNamespace(logged_in=lambda: True), history=fresh_queue(), playing=lambda kind, dbid: (kind, dbid) == ('movie', 1))
marks.on_update(json.dumps({'item': {'type': 'movie', 'id': 1}, 'playcount': 1}))
check('the open playback\'s own playcount bump is ignored', marks.history.entries == [])
RPC['VideoLibrary.GetMovieDetails'] = {'moviedetails': {'movieid': 1, 'title': 'X', 'year': 2020, 'uniqueid': {'imdb': 'tt1'}, 'file': r'D:\x.mkv', 'runtime': 5400, 'playcount': 0, 'lastplayed': ''}}
marks = Marks(auth=types.SimpleNamespace(logged_in=lambda: True), history=fresh_queue(), playing=lambda kind, dbid: False)
marks.guard('movie', 1)
marks.on_update(json.dumps({'item': {'type': 'movie', 'id': 1}, 'playcount': 1}))
check('guard: a watched count inside the guard window is the playback\'s own, ignored', marks.history.entries == [])
marks.on_update(json.dumps({'item': {'type': 'movie', 'id': 1}, 'playcount': 0}))
check('guard: an unwatch inside the guard window is the user\'s, queued', kinds(marks.history) == [h.UNMARK])
CONDS['Library.IsScanningVideo'] = True
marks = Marks(auth=types.SimpleNamespace(logged_in=lambda: True), history=fresh_queue(), playing=lambda kind, dbid: False)
marks.on_update(json.dumps({'item': {'type': 'movie', 'id': 1}, 'playcount': 1}))
CONDS['Library.IsScanningVideo'] = False
check('scan: a mark made while a scan runs counts; Kodi flags the scan\'s own counts (transaction), the scanning state says nothing about whose a mark is', kinds(marks.history) == [h.MARK])
CONDS['Library.IsScanningVideo'] = True
scan_ts = time.time() - 1
marks = Marks(auth=types.SimpleNamespace(logged_in=lambda: True), history=fresh_queue(), playing=lambda kind, dbid: False)
marks.on_update(json.dumps({'item': {'type': 'movie', 'id': 1}, 'playcount': 0}), arrived=scan_ts)
CONDS['Library.IsScanningVideo'] = False
check('scan: an unmark made while a scan runs is queued and goes into the ledger', kinds(marks.history) == [h.UNMARK] and marks.history.unmarked.get('movie:1') == scan_ts)
q = fresh_queue()
real_marks = Marks(auth=types.SimpleNamespace(logged_in=lambda: True), history=q, playing=lambda kind, dbid: False)
scrobbler = scr.Scrobbler(api=None, auth=passing, player=FakePlayer(), sender=FakeSender(), history=q, marks=real_marks)
early = scr.Session(1)
early.item, early.started, early.total, early.position = dict(ITEM), True, 3600.0, 360.0
scrobbler._stop(early, 10.0)
real_marks.on_update(json.dumps({'item': {'type': 'movie', 'id': 1}, 'playcount': 1}))
after_early = kinds(q)
q.entries = []
late = scr.Session(2)
late.item, late.started, late.total, late.position = dict(ITEM), True, 3600.0, 3500.0
scrobbler._stop(late, 97.0)
real_marks.on_update(json.dumps({'item': {'type': 'movie', 'id': 1}, 'playcount': 1}))
check('guard: a stop Kodi does not count as watched (10%) sets no guard, so a mark right after it is the user\'s; a stop at 97% guards Kodi\'s own bump',
      after_early == [h.MARK] and kinds(q) == [h.WATCH])
advanced = {}
xbmcvfs_module = sys.modules['xbmcvfs']
real_translate = xbmcvfs_module.translatePath
xbmcvfs_module.translatePath = lambda p: advanced.get(p, p)
system_file, master_file, profile_file = (os.path.join(PROFILE, name + '_advancedsettings.xml') for name in ('system', 'master', 'profile'))
advanced.update({'special://xbmc/system/advancedsettings.xml': system_file, 'special://masterprofile/advancedsettings.xml': master_file, 'special://profile/advancedsettings.xml': profile_file})


def advanced_xml(path, body):
    with open(path, 'w', encoding='utf-8') as handle:
        handle.write('<advancedsettings>%s</advancedsettings>' % body)


shares = [playerlib.kodi_watched_percent()]                                                              # no file: 90
advanced_xml(master_file, '<video><playcountminimumpercent>75</playcountminimumpercent></video>')
shares.append(playerlib.kodi_watched_percent())                                                          # the master profile's: 75
advanced_xml(profile_file, '<video><ignorepercentatend>5</ignorepercentatend></video>')
shares.append(playerlib.kodi_watched_percent())                                                          # the profile has a file without the share: it replaces the master's, 90
advanced_xml(profile_file, '<video><playcountminimumpercent>101</playcountminimumpercent></video>')
shares.append(playerlib.kodi_watched_percent())                                                          # the profile's: 101
advanced_xml(system_file, '<video><playcountminimumpercent>80</playcountminimumpercent></video>')
advanced_xml(profile_file, '<video><playcountminimumpercent>nope')
shares.append(playerlib.kodi_watched_percent())                                                          # a broken profile file: the system's 80 stands
advanced_xml(profile_file, '<video><playcountminimumpercent>250</playcountminimumpercent></video>')
shares.append(playerlib.kodi_watched_percent())                                                          # clamped as Kodi clamps it
for path in (system_file, master_file, profile_file):
    os.remove(path)
xbmcvfs_module.translatePath = real_translate
check('guard: Kodi\'s own watched share is read as Kodi reads it: the system file, then the profile\'s file when there is one, else the master profile\'s; 90 without any',
      shares == [90, 75, 90, 101, 80, 101] and scrobbler.watched_percent == 90)
outcomes = []
for share, progress in ((101, 95.0), (0, 10.0), (0, 95.0), (90, 95.0)):
    q = fresh_queue()
    real_marks = Marks(auth=types.SimpleNamespace(logged_in=lambda: True), history=q, playing=lambda kind, dbid: False)
    scrobbler = scr.Scrobbler(api=None, auth=passing, player=FakePlayer(), sender=FakeSender(), history=q, marks=real_marks)
    scrobbler.watched_percent = share
    session = scr.Session(3)
    session.item, session.started, session.total, session.position = dict(ITEM), True, 3600.0, 36.0 * progress
    scrobbler._stop(session, progress)
    real_marks.on_update(json.dumps({'item': {'type': 'movie', 'id': 1}, 'playcount': 1}))
    outcomes.append(h.MARK in kinds(q))
check('guard: with Kodi set never to count a playback (101, or 0) no stop sets a guard, so the user\'s mark right after it is queued; at 90 a stop at 95% guards the bump',
      outcomes == [True, True, True, False])
q = fresh_queue()
real_marks = Marks(auth=types.SimpleNamespace(logged_in=lambda: True), history=q, playing=lambda kind, dbid: False)
player = FakePlayer(position=360.0, total=3600.0)
scrobbler = scr.Scrobbler(api=None, auth=passing, player=player, sender=FakeSender(), history=q, marks=real_marks)
left_alone = scr.Session(4)
left_alone.item, left_alone.ignored = {'kind': 'movie', 'dbid': 1}, True
scrobbler.session = left_alone
scrobbler.tick(time.time() + 1)
scrobbler._stop(left_alone, scrobbler._progress(left_alone))
real_marks.on_update(json.dumps({'item': {'type': 'movie', 'id': 1}, 'playcount': 1}))
early_alone = (round(left_alone.position), h.MARK in kinds(q))
q.entries = []
left_alone = scr.Session(5)
left_alone.item, left_alone.ignored = {'kind': 'movie', 'dbid': 1}, True
scrobbler.session = left_alone
player.position = 3500.0
scrobbler.tick(time.time() + 2)
scrobbler._stop(left_alone, scrobbler._progress(left_alone))
real_marks.on_update(json.dumps({'item': {'type': 'movie', 'id': 1}, 'playcount': 1}))
check('guard: a library playback left alone (an excluded .strm) is still sampled, so an early stop sets no guard and the user\'s mark goes through, while one Kodi counts is guarded',
      early_alone == (360, True) and kinds(q) == [])
RPC.pop('VideoLibrary.GetMovieDetails')

# --- the Simkl context entry also sets Kodi's own play state (items already in that state are left alone) ---
calls_seen = []
utils.jsonrpc_batch = lambda calls: calls_seen.extend(calls) or [{'result': 'OK'} for _ in calls]
refused = playerlib.set_watched([dict(ITEM, playcount=0), dict(ITEM, key='movie:2', dbid=2, playcount=1), {'kind': 'episode', 'dbid': 7, 'playcount': 0}], True)
no_resume = {'resume': {'position': 0, 'total': 0}}
check('ctx: Kodi follows a Simkl mark as its own Mark as watched does (count, date, the resume point cleared), the item already watched is skipped',
      refused == 0 and [c[1] for c in calls_seen] == [dict({'movieid': 1, 'playcount': 1, 'lastplayed': calls_seen[0][1].get('lastplayed')}, **no_resume),
                                                      dict({'episodeid': 7, 'playcount': 1, 'lastplayed': calls_seen[1][1].get('lastplayed')}, **no_resume)]
      and calls_seen[0][0] == 'VideoLibrary.SetMovieDetails' and calls_seen[1][0] == 'VideoLibrary.SetEpisodeDetails' and len(calls_seen[0][1]['lastplayed']) == 19)
calls_seen.clear()
playerlib.set_watched([dict(ITEM, playcount=1), dict(ITEM, key='movie:2', dbid=2, playcount=0)], False)
check('ctx: an unmark clears the count, the date and the resume point, as Kodi\'s own unmark does',
      calls_seen == [('VideoLibrary.SetMovieDetails', dict({'movieid': 1, 'playcount': 0, 'lastplayed': ''}, **no_resume))])
file_calls = []
jsonrpc_before_files = utils.jsonrpc
utils.jsonrpc = lambda method, params=None: file_calls.append((method, params)) or {}
playerlib.set_file_watched(r'D:\x.mkv', False)
playerlib.set_file_watched(r'D:\x.mkv', True)
utils.jsonrpc = jsonrpc_before_files
check('ctx: a file outside the library gets the same three fields',
      file_calls[0] == ('Files.SetFileDetails', dict({'file': r'D:\x.mkv', 'media': 'video', 'playcount': 0, 'lastplayed': ''}, **no_resume))
      and file_calls[1][1]['playcount'] == 1 and len(file_calls[1][1]['lastplayed']) == 19 and file_calls[1][1]['resume'] == no_resume['resume'])
emitted = []
kodi_player = playerlib.Player(emitted.append)
kodi_player.playback_id = 3
for speed in (0, 2, -2, 1):
    kodi_player.onPlayBackSpeedChanged(speed)
check('player: a tempo under 1.0x (Kodi reports it as 0) arrives as speed 1, fast-forward and rewind pass through', [e[2] for e in emitted] == [1, 2, -2, 1] and emitted[0][:2] == ('speed', 3))
utils.jsonrpc_batch = lambda calls: None
check('ctx: a refused batch is reported as refused writes', playerlib.set_watched([dict(ITEM, playcount=0)], True) == 1)
utils.jsonrpc_batch = real_batch

# --- ratings made in Kodi's information dialog, and the prompt after a movie (service.py) ---
sys.argv = ['service.py']
service_module = importlib.import_module('resources.lib.service')
ident(token='simkl_at_A', account_id='A')
service = service_module.SimklService()
service.sender = FakeSender()
service.history.entries = []
rated = []
real_rate = service._rate
# recorded and passed on: the real method queues the rating, which the checks below read back from the queue
service._rate = lambda target, rating, account: rated.append((target, rating, account)) or real_rate(target, rating, account)


def ratings_queued(queue):
    """(bucket, ids, label, rating) of the queued ratings, oldest first."""
    return [(e['item']['bucket'], e['item']['ids'], e['item']['label'], e['rating']) for e in queue.entries if e['kind'] == h.RATE]


def info_dialog(dbtype, dbid, *readings):
    """The information dialog shown for an item: the user-rating label at each reading, then the dialog closes."""
    CONDS['Window.IsVisible(movieinformation)'] = True
    for reading in readings:
        LABELS.update({'ListItem.DBTYPE': dbtype, 'ListItem.DBID': str(dbid), 'ListItem.UserRating': reading})
        service._watch_rating()
    CONDS['Window.IsVisible(movieinformation)'] = False
    service._watch_rating()


RPC['VideoLibrary.GetMovieDetails'] = {'moviedetails': {'movieid': 1, 'title': 'X', 'year': 2020, 'uniqueid': {'imdb': 'tt1'}, 'file': r'D:\x.mkv', 'runtime': 5400, 'playcount': 1}}
info_dialog('movie', 1, '', '8')
check('ratings: a rating set in the information dialog is queued when the dialog closes, on the loop thread, with no trip through the sender',
      rated == [(('movies', {'imdb': 'tt1'}, 'X (2020)'), 8, 'A')] and ratings_queued(service.history) == [('movies', {'imdb': 'tt1'}, 'X (2020)', 8)] and service.sender.jobs == [])
rated.clear()
service.history.entries = []
info_dialog('movie', 1, '8')
check('ratings: an unchanged rating sends nothing', service.sender.jobs == [] and rated == [] and service.history.entries == [])
RPC['VideoLibrary.GetMovieDetails']['moviedetails']['userrating'] = 8
info_dialog('movie', 1, '', '')
lagging = list(rated)
rated.clear()
RPC['VideoLibrary.GetMovieDetails']['moviedetails']['userrating'] = 0
info_dialog('movie', 1, '', '6')
check('ratings: the labels a turn behind the pick (the dialog closed at once) take the rating the library holds; a library still at the opening value (the dialog open at Kodi\'s exit) leaves the sampled one',
      [r[1] for r in lagging] == [8] and [r[1] for r in rated] == [6])
rated.clear()
service.history.entries = []
RPC['VideoLibrary.GetMovieDetails']['moviedetails'].pop('userrating')
RPC['VideoLibrary.GetTVShowDetails'] = {'tvshowdetails': {'tvshowid': 77, 'title': 'S', 'uniqueid': {'tvdb': '9'}}}
info_dialog('tvshow', 77, '0', '7')
check('ratings: a library answer without the rating keeps the sampled value (a show, read without the cache)', rated == [(('shows', {'tvdb': '9'}, 'S'), 7, 'A')])
RPC.pop('VideoLibrary.GetTVShowDetails')
rated.clear()
service.history.entries = []
LABELS.clear()
LABELS['System.BuildVersion'] = '21.2'
RPC.pop('VideoLibrary.GetMovieDetails')

service.player = types.SimpleNamespace(isPlayingVideo=lambda: False)
PICK[0] = 3
service._after_watch(dict(ITEM), 'A')
for _ in range(50):
    if rated:
        break
    time.sleep(0.05)
check('ratings: the prompt after a movie queues the pick from its own thread (position 3 = 8, behind "Not now"), opened on "Not now"',
      rated == [(('movies', {'imdb': 'tt1'}, 'X (2020)'), 8, 'A')] and ratings_queued(service.history) == [('movies', {'imdb': 'tt1'}, 'X (2020)', 8)] and PRESELECT[0] == 0)
rated.clear()
service.history.entries = []
PRESELECT[0] = -1
service._after_watch(dict(ITEM, userrating=7), 'A')
service._after_watch(dict(ITEM, playcount=1), 'A')
time.sleep(0.2)
check('ratings: no prompt for a movie rated in Kodi already or watched before', service.sender.jobs == [] and rated == [] and PRESELECT[0] == -1)
service._after_watch(dict(EPISODE), 'A')
SETTINGS['rate_prompt'] = 'false'
service._after_watch(dict(ITEM), 'A')
time.sleep(0.2)
check('ratings: no prompt for an episode or with the switch off', service.sender.jobs == [] and rated == [])
SETTINGS.pop('rate_prompt')

# --- 'Your rating' picked over a Home widget: the select dialog answers the labels meanwhile ---
rated.clear()
service.history.entries = []
RPC['VideoLibrary.GetMovieDetails'] = {'moviedetails': {'movieid': 1, 'title': 'X', 'year': 2020, 'uniqueid': {'imdb': 'tt1'}, 'file': r'D:\x.mkv', 'runtime': 5400, 'playcount': 1}}
CONDS['Window.IsVisible(movieinformation)'] = True
LABELS.update({'ListItem.DBTYPE': 'movie', 'ListItem.DBID': '1', 'ListItem.UserRating': ''})
service._watch_rating()
LABELS.update({'ListItem.DBTYPE': '', 'ListItem.DBID': '', 'ListItem.UserRating': ''})
service._watch_rating()
LABELS.update({'ListItem.DBTYPE': 'movie', 'ListItem.DBID': '1', 'ListItem.UserRating': '9'})
service._watch_rating()
CONDS['Window.IsVisible(movieinformation)'] = False
service._watch_rating()
check('ratings: a rating picked over a Home widget (the pick dialog blanks the labels meanwhile) still goes to Simkl', rated == [(('movies', {'imdb': 'tt1'}, 'X (2020)'), 9, 'A')])
rated.clear()
service.history.entries = []
LABELS.clear()
LABELS['System.BuildVersion'] = '21.2'
RPC.pop('VideoLibrary.GetMovieDetails')


# --- a bulk unmark is judged by Kodi's announcement times, consecutive ones less than a second apart ---
def unmarks(queue, stamps):
    for index, stamp in enumerate(stamps):
        queue.add_unmark(dict(ITEM, key='movie:%d' % (index + 1), dbid=index + 1), arrived=stamp)


base = time.time() - 60
q = fresh_queue()
unmarks(q, [base + 0.1 * i for i in range(24)])
q._settle_window(q.window_last + 5)
check('bulk: 24 unmarks announced 0.1 s apart (2.4 s in all, handled whenever) are one bulk unmark, dropped', q.entries == [])
q = fresh_queue()
unmarks(q, [base, base + 1.5])
q._settle_window(q.window_last + 5)
answer_all(q)
check('bulk: two unmarks 1.5 s apart are two single ones, both sent', kinds(q) == [h.UNMARK, h.UNMARK] and q._eligible() == q.entries)
q = fresh_queue()
unmarks(q, [base + 0.05 * i for i in range(10)] + [base + 3.0])
q._settle_window(q.window_last + 5)
answer_all(q)
check('bulk: a burst of ten and one single 3 s later: the ten are dropped, the single goes out', [e['key'] for e in q.entries] == ['movie:11'] and q._eligible() == q.entries)


# --- a file only the search can name, watched while Simkl is unreachable: written ahead, named at the flush ---
class OfflineApi:
    def call(self, method, path, body=None, token=None, timeout=30):
        raise ApiError(0, 'transport')


q = fresh_queue()
scrobbler = scr.Scrobbler(api=OfflineApi(), auth=passing, player=FakePlayer(), sender=FakeSender(), history=q, marks=FakeMarks())
session = scr.Session(13)
session.item = {'kind': None, 'key': r'file:D:\TV\Gnosia\Gnosia - 02.mkv', 'dbid': None, 'title': 'Gnosia - 02', 'year': None, 'ids': {}, 'file': r'D:\TV\Gnosia\Gnosia - 02.mkv', 'runtime': 0, 'label': 'Gnosia - 02.mkv'}
session.resolve_first = True
session.started = True
scrobbler._stop(session, 100.0)
eid = q.entries[0]['id'] if q.entries else None
check('offline: the watch of a file not named yet is written ahead', kinds(q) == [h.WATCH] and q.entries[0]['inflight'] and not q.entries[0]['item'].get('ids'))
scrobbler._send(session, 'stop', 100.0, eid)
check('offline: a stop whose file search failed releases it to the queue', kinds(q) == [h.WATCH] and not q.entries[0]['inflight'])
real_search = playerlib.search_file
playerlib.search_file = lambda api, auth, account, item: (_ for _ in ()).throw(ApiError(403, 'forbidden'))
first_wait = q._named(q._eligible(), 'A') == [] and q.entries[0].get('retry_wait') == h.BACKOFF_MIN and q.entries[0]['retry_at'] > time.time() and q._eligible() == []
q.entries[0]['retry_at'] = 0
playerlib.search_file = lambda api, auth, account, item: (_ for _ in ()).throw(ApiError(400, 'invalid_file'))
q._named(q._eligible(), 'A')
check('offline: a file search Simkl refuses holds its entry back alone, 30 s then 60, and tells the queue when to look again',
      first_wait and kinds(q) == [h.WATCH] and q.entries[0]['retry_wait'] == 2 * h.BACKOFF_MIN and q._next_retry() == q.entries[0]['retry_at'])
q.due = 0
q.tick(time.time() + 5, FakeSender())
check('offline: a tick with nothing else to send waits for that entry\'s own retry', q.due == q.entries[0]['retry_at'] and not q.flushing)
q.entries[0]['retry_at'] = 0
playerlib.search_file = lambda api, auth, account, item: playerlib.remote_episode({'simkl': '9'}, 'Gnosia', '', 2023, 1, 2, item['file'])
named = q._named(q._eligible(), 'A')
check('offline: once named, the waits of its own are gone', 'retry_at' not in q.entries[0] and 'retry_wait' not in q.entries[0])
check('offline: once named, the entry carries the episode and goes out', [e['item']['kind'] for e in named] == ['episode'] and q.entries[0]['key'] == named[0]['key'] and named[0]['item']['ids'] == {'simkl': '9'})
q = fresh_queue()
q.watch_event(dict(session.item), time.time(), False)
q.release(q.entries[0]['id'], toast=False)
playerlib.search_file = lambda api, auth, account, item: None
check('offline: a file Simkl does not know is dropped at the flush', q._named(q._eligible(), 'A') == [] and q.entries == [])
q = fresh_queue()
for number in range(12):
    q.release(q.watch_event(dict(session.item, key='file:%d' % number, file=r'D:\%d.mkv' % number), time.time() - 10, False), toast=False)
searches = []
playerlib.search_file = lambda api, auth, account, item: searches.append(item['file']) or (_ for _ in ()).throw(ApiError(0, 'transport'))
q.backoff = h.BACKOFF_MIN
before = time.time()
named = q._named(q._eligible(), 'A')
transport = named is None and len(searches) == 1 and q.due >= before + h.BACKOFF_MIN and q.backoff == 2 * h.BACKOFF_MIN and not any('retry_at' in e for e in q.entries)
searches.clear()
playerlib.search_file = lambda api, auth, account, item: searches.append(item['file']) or (_ for _ in ()).throw(ApiError(500, 'server_error'))
named = q._named(q._eligible(), 'A')
check('offline: a search failing by transport stops the flush at once (one search, the queue backs off as a whole, no wait per entry), a server error the same',
      transport and named is None and len(searches) == 1 and q.backoff == 4 * h.BACKOFF_MIN and not any('retry_at' in e for e in q.entries) and len(q.entries) == 12)
playerlib.search_file = real_search

# --- credentials inside wrapper paths, excluded folders behind them ---
stack = 'stack://smb://user:secret@nas/Movies/Film-cd1.avi , smb://user:secret@nas/Movies/Film-cd2.avi'
archive = 'rar://smb%3a%2f%2fuser%3asecret%40nas%2fMovies%2fFilm.rar/Film.mkv'
check('paths: credentials are stripped inside a stack and an archive path',
      playerlib.sanitized(stack) == 'stack://smb://nas/Movies/Film-cd1.avi , smb://nas/Movies/Film-cd2.avi' and 'secret' not in playerlib.sanitized(archive) and playerlib.sanitized(archive).endswith('/Film.mkv'))
SETTINGS.update({'exclude_path1_on': 'true', 'exclude_path1': r'D:\Private', 'exclude_path2_on': 'true', 'exclude_path2': 'smb://nas/Private/'})
check('paths: an excluded folder covers its stacked and archived items',
      playerlib.excluded(r'stack://D:\Private\Film-cd1.avi , D:\Private\Film-cd2.avi') == 'excluded folder' and playerlib.excluded('zip://smb%3a%2f%2fnas%2fPrivate%2fFilm.zip/Film.mkv') == 'excluded folder'
      and playerlib.excluded(r'D:\Private2\x.mkv') == '' and playerlib.excluded('stack://smb://nas/Movies/a.avi , smb://nas/Movies/b.avi') == '')
for key in ('exclude_path1_on', 'exclude_path1', 'exclude_path2_on', 'exclude_path2'):
    SETTINGS.pop(key)

# --- the plugin exclusion sees the add-on URL (mediapath) on a first play; ids survive a replay; stale window ids ---
RPC['Player.GetActivePlayers'] = [{'playerid': 1, 'type': 'video'}]
SETTINGS['exclude_plugin'] = 'true'
RPC['Player.GetItem'] = {'item': {'type': 'movie', 'uniqueid': {'tmdb': '555'}, 'title': 'X', 'file': 'https://cdn/x.mp4', 'mediapath': 'plugin://plugin.video.x/play'}}
check('identify: a first play of an add-on item is excluded by its mediapath', playerlib.identify(FakePlayer(path='https://cdn/x.mp4')) == ('skip', 'plugin'))
SETTINGS.pop('exclude_plugin')
RPC['Player.GetItem'] = {'item': {'type': 'unknown', 'file': 'plugin://plugin.video.x/play', 'label': 'X'}}
LABELS['VideoPlayer.UniqueID(tmdb)'] = '603'
status, payload = playerlib.identify(FakePlayer(path='https://cdn/x.mp4'))
check('identify: a replayed add-on item (Kodi answers its files row, no uniqueid) is named by the playing tag', status == 'ok' and payload['ids'] == {'tmdb': '603'})
LABELS.pop('VideoPlayer.UniqueID(tmdb)')
WINDOW['script.trakt.ids'] = '{"tmdb": "550"}'
RPC['Player.GetItem'] = {'item': {'type': 'unknown', 'file': r'D:\Home videos\birthday.mkv', 'label': 'birthday'}}
status, payload = playerlib.identify(FakePlayer(path=r'D:\Home videos\birthday.mkv'))
check('identify: a local file ignores the ids an add-on left in the window properties', status == 'file')
RPC['Player.GetItem'] = {'item': {'type': 'unknown', 'file': 'https://cdn/y.mp4', 'label': 'y'}}
status, payload = playerlib.identify(FakePlayer(path='https://cdn/y.mp4'))
check('identify: a stream still takes them', status == 'ok' and payload['ids'] == {'tmdb': '550'})
WINDOW.pop('script.trakt.ids')
RPC.pop('Player.GetItem')
RPC.pop('Player.GetActivePlayers')

# --- the 3.x minimum length was seconds under a minutes label; 4.0 declares 0..600, so Kodi answers the default for a bigger stored value ---
for stored, expect in (('300', '5'), ('5', '5'), ('45', '1')):
    SETTINGS['migrated_v4'] = 'false'
    SETTINGS['min-length'] = stored
    settings.migrate()
    check('migrate: a 3.x minimum length of %s becomes %s minutes' % (stored, expect), SETTINGS['min-length'] == expect and SETTINGS['migrated_v4'] == 'true')
settings_xml = os.path.join(PROFILE, 'settings.xml')
for stored, answered, expect in (('900', '5', '15'), ('1200', '5', '20'), ('300', '300', '5'), ('20', '20', '20'), ('junk', '7', '7'), ('90000', '5', '600')):
    with open(settings_xml, 'w', encoding='utf-8') as handle:
        handle.write('<settings version="2">\n    <setting id="scr-pct">70</setting>\n    <setting id="min-length">%s</setting>\n</settings>\n' % stored)
    SETTINGS['migrated_v4'] = 'false'
    SETTINGS['min-length'] = answered
    settings.migrate()
    check('migrate: a stored minimum length of %s (Kodi answering %s) becomes %s minutes' % (stored, answered, expect), SETTINGS['min-length'] == expect)
with open(settings_xml, 'w', encoding='utf-8') as handle:
    handle.write('<settings>\n    <setting id="min-length" value="900" />\n</settings>\n')
SETTINGS['migrated_v4'] = 'false'
SETTINGS['min-length'] = '5'
settings.migrate()
check('migrate: the Kodi 18 settings format is read as well', SETTINGS['min-length'] == '15')
os.remove(settings_xml)
SETTINGS.pop('min-length')

# --- the Simkl menu offers Mark/Remove on library items whose type is not tracked ---
context_module = importlib.import_module('context')
SETTINGS['scrobble_movies'] = 'false'
check('context: with movie tracking off Kodi\'s own mark cannot reach Simkl, so the Simkl entries show for a library movie',
      not context_module._kodi_marks_reach_simkl(_Addon(), {'dbid': 5, 'dbtype': 'movie'}) and context_module._kodi_marks_reach_simkl(_Addon(), {'dbid': 5, 'dbtype': 'episode'}))
SETTINGS.pop('scrobble_movies')

# --- the Simkl menu on an add-on's episode (its show's ids from the item's properties); the rating pick refused before it is shown ---
ident(token='simkl_at_A', account_id='A')
LABELS.update({'ListItem.DBTYPE': 'episode', 'ListItem.DBID': '0', 'ListItem.UniqueID(tvdb)': '555', 'ListItem.Season': '0', 'ListItem.Episode': '2', 'ListItem.TVShowTitle': 'S', 'ListItem.Label': 'E',
               'ListItem.Property(script.simkl.ids)': '{"tmdb": "1399"}'})
payload = focus.from_infolabels()
check('dev: an add-on episode row carries its show\'s ids from the JSON property and keeps a special\'s season 0',
      payload['show_ids'] == {'tmdb': '1399'} and payload['season'] == 0 and payload['ids'] == {'tvdb': '555'} and payload['dbid'] == 0)
LABELS.update({'ListItem.Property(script.simkl.ids)': 'not json', 'ListItem.Property(tvshow.tvdb_id)': '81189', 'ListItem.Season': ''})
payload = focus.from_infolabels()
check('dev: invalid JSON counts as nothing, the flat tvshow.*_id properties stand in (normalised), an unknown season is -1', payload['show_ids'] == {'tvdb': '81189'} and payload['season'] == -1)
LABELS.pop('ListItem.Property(tvshow.tvdb_id)')
LABELS.update({'ListItem.Property(script.simkl.ids)': '', 'ListItem.Property(script.trakt.ids)': '{"trakt": 1390, "slug": "got"}', 'ListItem.Property(tvshow.tmdb_id)': '1399'})
payload = focus.from_infolabels()
LABELS.pop('ListItem.Property(tvshow.tmdb_id)')
alone = focus.from_infolabels()
check('dev: an id object without Simkl ids (trakt, slug) does not count, the flat tvshow.tmdb_id stands in; with nothing usable show_ids is empty',
      payload['show_ids'] == {'tmdb': '1399'} and alone['show_ids'] == {})
EPISODE_ROW = {'dbtype': 'episode', 'dbid': 0, 'ids': {'tvdb': '555'}, 'showtitle': 'S', 'title': 'E', 'season': 1, 'number': 2}
service.history.entries = []
service._context('ctx_mark', dict(EPISODE_ROW, show_ids={'tmdb': '1399'}))
marked = list(service.history.entries)
planned = []
service._plan_to_watch = lambda target, account: planned.append(target)
service.sender.jobs.clear()
service._context('ctx_plantowatch', dict(EPISODE_ROW, show_ids={'tmdb': '1399'}))
for job in service.sender.jobs:
    job()
del service._plan_to_watch
toasts = []
utils.notify = lambda message, *a, **k: toasts.append(message)
service.history.entries = []
service._context('ctx_mark', dict(EPISODE_ROW))
service._context('ctx_mark', dict(EPISODE_ROW, show_ids={'tmdb': '1399'}, number=0))
utils.notify = real_notify
check('context: an add-on episode with its show\'s ids is marked by them (its uniqueid as the episode\'s) and Plan to watch targets the show; without them, or without a number, nothing is queued and the entry says so',
      [e['kind'] for e in marked] == [h.MARK] and marked[0]['item']['ids'] == {'tmdb': '1399'} and marked[0]['item']['season'] == 1 and marked[0]['item']['number'] == 2
      and marked[0]['item']['episode_ids'] == {'tvdb': '555'} and planned == [('shows', {'tmdb': '1399'}, 'S')] and toasts == ['str32271 {0}', 'str32257 {0}'] and service.history.entries == [])
service.sender.jobs.clear()


class _Tag:
    def __init__(self, fields):
        self.fields = fields

    def getUniqueID(self, key):
        return self.fields.get('uniqueid', {}).get(key, '')

    def getDbId(self):
        return self.fields.get('dbid', -1)

    def getMediaType(self):
        return self.fields.get('dbtype', '')

    def getUserRating(self):
        return self.fields.get('userrating', 0)

    def getSeason(self):
        return self.fields.get('season', -1)

    def getEpisode(self):
        return self.fields.get('number', -1)

    def getTVShowTitle(self):
        return self.fields.get('showtitle', '')

    def getTitle(self):
        return self.fields.get('title', '')

    def getYear(self):
        return self.fields.get('year', 0)


class _ListItem:
    """sys.listitem of the context menu: the focused row and its properties."""
    def __init__(self, fields, properties=None):
        self.fields = fields
        self.properties = properties or {}

    def getVideoInfoTag(self):
        return _Tag(self.fields)

    def getPath(self):
        return self.fields.get('file', '')

    def getLabel(self):
        return self.fields.get('label', '')

    def getProperty(self, name):
        return self.properties.get(name, '')


def script_run(argv):
    """default_script.main() with these arguments: (select dialogs shown, NotifyAll builtins sent, the last toast)."""
    sys.argv = ['default.py'] + argv
    SELECTS[0] = 0
    BUILTINS.clear()
    NOTIFICATIONS.clear()
    default_script.main()
    return SELECTS[0], list(BUILTINS), NOTIFICATIONS[-1][1] if NOTIFICATIONS else ''


def menu_run(listitem, pick):
    """context.main() on this row with the pick-th entry chosen: (select dialogs shown, NotifyAll builtins sent, the last toast)."""
    sys.listitem = listitem
    PICK[0] = pick
    SELECTS[0] = 0
    BUILTINS.clear()
    NOTIFICATIONS.clear()
    context_module.main()
    return SELECTS[0], list(BUILTINS), NOTIFICATIONS[-1][1] if NOTIFICATIONS else ''


RPC['VideoLibrary.GetMovieDetails'] = {'moviedetails': {'movieid': 1, 'title': 'X', 'year': 2020, 'playcount': 0}}
movie_row = _ListItem({'dbid': 1, 'dbtype': 'movie', 'label': 'X (2020)', 'title': 'X', 'year': 2020, 'uniqueid': {'imdb': 'tt1'}})
refused_menu = menu_run(movie_row, 1)   # a library movie's menu: Plan to watch, Rate on Simkl, Sync updates
RPC['VideoLibrary.GetMovieDetails']['moviedetails']['playcount'] = 1
offered_menu = menu_run(movie_row, 0)   # a watched movie's menu: Rate on Simkl, Sync updates (Plan to watch is left out)
check('rate: the Simkl menu refuses the pick for a library movie Kodi has as unwatched (one select: the menu) and offers it once the movie is watched',
      refused_menu == (1, [], 'str32299 X (2020)') and offered_menu[0] == 2 and len(offered_menu[1]) == 1 and offered_menu[1][0].startswith('NotifyAll(script.simkl, ctx_rate, '))
LABELS.clear()
LABELS.update({'System.BuildVersion': '21.2', 'ListItem.DBTYPE': 'movie', 'ListItem.DBID': '1', 'ListItem.Label': 'X (2020)', 'ListItem.Title': 'X', 'ListItem.Year': '2020', 'ListItem.UniqueID(imdb)': 'tt1'})
RPC['VideoLibrary.GetMovieDetails']['moviedetails']['playcount'] = 0
outcomes = [script_run(argv) for argv in (['action=rate'], ['action=rate', 'rating=0'], ['action=rate', 'rating=5'])]
RPC['VideoLibrary.GetMovieDetails']['moviedetails']['playcount'] = 1
watched_run = script_run(['action=rate'])
check('rate: RunScript on an unwatched library movie shows no pick and sends nothing; rating=0 (a removal) and rating=5 go to the service as before; once watched the pick is shown',
      [(o[0], len(o[1]), o[2]) for o in outcomes] == [(0, 0, 'str32299 X (2020)'), (0, 1, ''), (0, 1, '')] and watched_run[0] == 1 and len(watched_run[1]) == 1)
RPC.pop('VideoLibrary.GetMovieDetails')
LABELS.clear()
LABELS.update({'System.BuildVersion': '21.2', 'ListItem.DBTYPE': 'episode', 'ListItem.DBID': '0', 'ListItem.UniqueID(tvdb)': '555', 'ListItem.Season': '1', 'ListItem.Episode': '2', 'ListItem.TVShowTitle': 'S', 'ListItem.Label': 'E'})
orphan_run = script_run(['action=mark'])
LABELS['ListItem.Property(script.trakt.ids)'] = '{"trakt": 1390, "slug": "got"}'
trakt_only = script_run(['action=rate'])
LABELS['ListItem.Property(script.trakt.ids)'] = '{"tvdb": "81189"}'
named_run = script_run(['action=mark'])
check('context: an id property with trakt ids only is as good as none: no rating pick, nothing sent, the entry says so', trakt_only == (0, [], 'str32271 S'))
sent = service_module._payload(named_run[1][0][len('NotifyAll(script.simkl, ctx_mark, '):-1]) if named_run[1] else {}
check('context: RunScript on an add-on episode without its show\'s ids says so and sends nothing; with script.trakt.ids on the item the mark goes out with them',
      orphan_run == (0, [], 'str32271 S') and sent.get('show_ids') == {'tvdb': '81189'} and sent.get('ids') == {'tvdb': '555'})
episode_fields = {'dbid': -1, 'dbtype': 'episode', 'label': 'E', 'uniqueid': {'tvdb': '555'}, 'season': 1, 'number': 2, 'showtitle': 'S'}
no_ids = menu_run(_ListItem(episode_fields), 3)   # an add-on episode's menu: Mark, Unmark, Plan to watch, Rate on Simkl, Sync updates
with_ids = menu_run(_ListItem(episode_fields, {'script.simkl.ids': '{"tmdb": "1399"}'}), 3)
check('context: the Simkl menu on an add-on episode refuses the rating pick without the show\'s ids (one select: the menu), rates the show with them',
      no_ids == (1, [], 'str32271 S') and with_ids[0] == 2 and len(with_ids[1]) == 1
      and service_module._payload(with_ids[1][0][len('NotifyAll(script.simkl, ctx_rate, '):-1]).get('show_ids') == {'tmdb': '1399'})
LABELS.clear()
LABELS['System.BuildVersion'] = '21.2'
BUILTINS.clear()

# --- the Simkl menu: Remove from Simkl history on seasons and shows; no Plan to watch on a watched library movie ---
ident(token='simkl_at_A', account_id='A')
menu = lambda payload: [name for name, _ in context_module._actions(_Addon(), payload)]  # noqa: E731
RPC['VideoLibrary.GetMovieDetails'] = {'moviedetails': {'movieid': 1, 'title': 'X', 'year': 2020, 'uniqueid': {'imdb': 'tt1'}, 'playcount': 1}}
watched_menu = menu({'dbid': 1, 'dbtype': 'movie'})
was_watched = focus.watched_movie({'dbid': 1, 'dbtype': 'movie'})
RPC['VideoLibrary.GetMovieDetails']['moviedetails']['playcount'] = 0
check('menu: a library season or show offers Remove from Simkl history (never Mark); an episode or a movie neither; a watched movie loses Plan to watch',
      menu({'dbid': 4, 'dbtype': 'season'}) == ['ctx_unmark', 'ctx_plantowatch', 'ctx_rate', 'import_check'] and menu({'dbid': 5, 'dbtype': 'tvshow'}) == ['ctx_unmark', 'ctx_plantowatch', 'ctx_rate', 'import_check']
      and menu({'dbid': 7, 'dbtype': 'episode'}) == ['ctx_plantowatch', 'ctx_rate', 'import_check'] and menu({'dbid': 1, 'dbtype': 'movie'}) == ['ctx_plantowatch', 'ctx_rate', 'import_check']
      and watched_menu == ['ctx_rate', 'import_check'] and was_watched and not focus.watched_movie({'dbid': 1, 'dbtype': 'movie'}) and not focus.watched_movie({'dbid': 0, 'dbtype': 'movie'}))
toasts = []
utils.notify = lambda message, *a, **k: toasts.append(message)
service.sender.jobs.clear()
RPC['VideoLibrary.GetMovieDetails']['moviedetails']['playcount'] = 1
service._context('ctx_plantowatch', {'dbtype': 'movie', 'dbid': 1})
blocked = (len(service.sender.jobs), list(toasts))
RPC['VideoLibrary.GetMovieDetails']['moviedetails']['playcount'] = 0
service._context('ctx_plantowatch', {'dbtype': 'movie', 'dbid': 1})
utils.notify = real_notify
LABELS.update({'ListItem.DBTYPE': 'movie', 'ListItem.DBID': '1', 'ListItem.Label': 'X (2020)', 'ListItem.Title': 'X', 'ListItem.Year': '2020', 'ListItem.UniqueID(imdb)': 'tt1'})
RPC['VideoLibrary.GetMovieDetails']['moviedetails']['playcount'] = 1
plan_run = script_run(['action=plantowatch'])
LABELS.clear()
LABELS['System.BuildVersion'] = '21.2'
check('plan to watch: a library movie Kodi has as watched is not moved, from the menu or RunScript (Simkl would take it out of the watched list); an unwatched one is',
      blocked == (0, ['str32314 X (2020)']) and len(service.sender.jobs) == 1 and plan_run == (0, [], 'str32314 X (2020)'))
service.sender.jobs.clear()
RPC.pop('VideoLibrary.GetMovieDetails')
playerlib._shows.clear()
RPC['VideoLibrary.GetSeasonDetails'] = {'seasondetails': {'tvshowid': 5, 'season': 1}}
RPC['VideoLibrary.GetTVShowDetails'] = {'tvshowdetails': {'tvshowid': 5, 'title': 'S', 'uniqueid': {'tvdb': '9'}}}
season_rows = [{'episodeid': 11, 'tvshowid': 5, 'season': 1, 'episode': 1, 'file': r'D:\S\E1.mkv', 'playcount': 1, 'uniqueid': {}},
               {'episodeid': 12, 'tvshowid': 5, 'season': 1, 'episode': 2, 'file': r'D:\S\E2.mkv', 'playcount': 1, 'uniqueid': {}}]
RPC['VideoLibrary.GetEpisodes'] = {'episodes': season_rows}
season_writes = []
saved_batch = utils.jsonrpc_batch
utils.jsonrpc_batch = lambda calls: season_writes.extend(calls) or [{'result': 'OK'} for _ in calls]
service.history.entries, service.history.unmarked, service.history.window_unmarks = [], {}, []
service._context('ctx_unmark', {'dbtype': 'season', 'dbid': 4})
utils.jsonrpc_batch = saved_batch
queued = [(e['kind'], e.get('explicit'), e['key']) for e in service.history.entries]
utils.jsonrpc = lambda method, params=None: ({'episodedetails': dict(next(r for r in season_rows if r['episodeid'] == params['episodeid']), playcount=0)}
                                             if method == 'VideoLibrary.GetEpisodeDetails' else real_jsonrpc(method, params))
echo_at = time.time()
service.marks.on_update(json.dumps({'item': {'type': 'episode', 'id': 11}, 'playcount': 0}), arrived=echo_at)
service.marks.on_update(json.dumps({'item': {'type': 'episode', 'id': 12}, 'playcount': 0}), arrived=echo_at + 0.1)
utils.jsonrpc = real_jsonrpc
windowed = list(service.history.window_unmarks)
service.history._settle_window(echo_at + 5)
real_api, real_auth = service.history.api, service.history.auth
service.history.api, service.history.auth = RemoveApi(), PassAuth()
service.history.due = 0
sender = FakeSender()
service.history.tick(time.time() + 5, sender)
for job in sender.jobs:
    job()
removed_body = service.history.api.removed[0] if service.history.api.removed else {}
service.history.api, service.history.auth = real_api, real_auth
check('menu: Remove from Simkl history on a season fans out to its episodes: two explicit unmarks, two playcount-0 writes, Kodi\'s echoes queue nothing, one request removes both',
      queued == [(h.UNMARK, True, 'episode:11'), (h.UNMARK, True, 'episode:12')] and [c[1]['episodeid'] for c in season_writes if c[0] == 'VideoLibrary.SetEpisodeDetails'] == [11, 12]
      and windowed == [] and sum(len(s['episodes']) for show in removed_body.get('shows', []) for s in show['seasons']) == 2 and service.history.entries == [])
for method in ('VideoLibrary.GetSeasonDetails', 'VideoLibrary.GetTVShowDetails', 'VideoLibrary.GetEpisodes'):
    RPC.pop(method)
service.history.unmarked = {}

# --- this box's own early stop comes back from Simkl a second later: not a new resume point ---
last = utils.kodi_date_to_ts('2026-01-01 10:00:00')
row = dict(ROW, runtime=7200, resume=3450, resume_total=6900, lastplayed='2026-01-01 10:00:00')
own = {'type': 'movie', 'progress': 50, 'paused_at': utils.utc_stamp(last + 1), 'movie': {'ids': {'imdb': 'tt1'}}}
index = index_with_movie()
index.movies['imdb:tt1'] = [row]
check('resume: a pause at Kodi\'s own bookmark is left alone', imp.Importer._resume_write(index, own) == [])
other = dict(own, progress=60, paused_at=utils.utc_stamp(last + 600))
check('resume: a later pause elsewhere lands by the file\'s real length, not the scraper runtime',
      imp.Importer._resume_write(index, other) == [(row, {'resume': {'position': 4140, 'total': 6900}, 'lastplayed': utils.ts_to_kodi_date(last + 600)})])
copy = dict(row, dbid=2, key='movie:2')
index.movies['imdb:tt1'] = [row, copy]
index.movies['tmdb:99'] = [row]
check('resume: a movie the library holds twice gets the pause on both copies; a row reached by two ids once',
      [item['dbid'] for item, _ in imp.Importer._resume_write(index, dict(other, movie={'ids': {'imdb': 'tt1', 'tmdb': '99'}}))] == [1, 2])

# --- a scheduled run that fails is tried again in 15 minutes, not at the next interval ---
importer._get = lambda path, timeout=30: (_ for _ in ()).throw(ApiError(0, 'transport'))
importer._save({})
before = time.time()
importer._run('check', False, True)
check('sync: after a scheduled run that got no answer the next try is due in 15 minutes',
      abs(importer.last_auto + imp._auto_interval() - (before + imp.AUTO_RETRY)) < 5 and abs(utils.parse_stamp(importer._load()['last_auto_run_at']) - importer.last_auto) < 1)
importer._get = lambda path, timeout=30: (_ for _ in ()).throw(ApiError(400, 'max_items', 'too many'))
before = time.time()
importer._run('check', False, True)
check('sync: a scheduled run Simkl refused waits for the next interval, no quarter-hour loop', abs(importer.last_auto - before) < 5)
importer.last_finish = 0

# --- an identity.json that cannot be read is not overwritten (no silent logout) ---
with open(os.path.join(PROFILE, 'identity.json'), 'w', encoding='utf-8') as handle:
    handle.write('{not json')
settings._identity = None
state = settings._identity_state()
with open(os.path.join(PROFILE, 'identity.json'), 'r', encoding='utf-8') as handle:
    kept = handle.read()
check('identity: an unreadable identity.json reads as logged out for this run and is left in place', state['token'] == '' and kept == '{not json')
settings._identity = None
ident(token='simkl_at_A', account_id='A')

# --- an import guard is consumed by its echo and cleared by the user's own unmark ---
RPC['VideoLibrary.GetMovieDetails'] = {'moviedetails': {'movieid': 1, 'title': 'X', 'year': 2020, 'uniqueid': {'imdb': 'tt1'}, 'file': r'D:\x.mkv', 'runtime': 5400, 'playcount': 1, 'lastplayed': '2026-01-01 10:00:00'}}
marks = Marks(auth=types.SimpleNamespace(logged_in=lambda: True), history=fresh_queue(), playing=lambda kind, dbid: False)
marks.guard('movie', 1, 600)
marks.on_update(json.dumps({'item': {'type': 'movie', 'id': 1}, 'playcount': 1}))
marks.on_update(json.dumps({'item': {'type': 'movie', 'id': 1}, 'playcount': 1}))
check('guard: the sync\'s write echoes once and consumes its guard; a second watched count is the user\'s', kinds(marks.history) == [h.MARK])
marks = Marks(auth=types.SimpleNamespace(logged_in=lambda: True), history=fresh_queue(), playing=lambda kind, dbid: False)
marks.guard('movie', 1, 600)
marks.on_update(json.dumps({'item': {'type': 'movie', 'id': 1}, 'playcount': 0}))
marks.on_update(json.dumps({'item': {'type': 'movie', 'id': 1}, 'playcount': 1}))
check('guard: the user\'s unmark clears a lingering guard, their mark after it counts (and supersedes the unmark)', kinds(marks.history) == [h.MARK])
RPC.pop('VideoLibrary.GetMovieDetails')


# --- a removal the user cancelled during the list check (re-marked meanwhile) must not go out ---
class CancelApi:
    def __init__(self, queue):
        self.queue = queue
        self.paths = []

    def call(self, method, path, body=None, token=None, timeout=30):
        self.paths.append(path)
        if path.startswith('/sync/watched'):
            self.queue.add_mark(ITEM)   # the user marks it again while Simkl is asked about the movie
            return [dict(item, result=True, list='completed', rewatches=0) for item in body]
        return {'added': {}, 'deleted': {}, 'not_found': {}}


ident(token='simkl_at_A', account_id='A')
asked = []
q = h.History(api=None, auth=PassAuth(), ask=asked.append)
q.entries, q.unmarked, q.window_unmarks = [], {}, []
q.api = CancelApi(q)
q.add_unmark(ITEM, explicit=True)
q.answer_unmark(asked[0], 'plan')
sender = FakeSender()
q.tick(time.time() + 5, sender)
sender.jobs[0]()
check('cancel: an unmark re-marked during the list check is neither sent nor remembered, the mark waits for the next flush',
      not any('/remove' in p or '/add-to-list' in p for p in q.api.paths) and kinds(q) == [h.MARK] and q.due is not None and ITEM['key'] not in q.unmarked)

# --- a playback left alone still owns Kodi's own watched bump ---
scrobbler = scr.Scrobbler(api=None, auth=passing, player=FakePlayer(), sender=FakeSender(), history=fresh_queue(), marks=FakeMarks())
session = scr.Session(14)
session.item = dict(ITEM)
session.ignored = True
scrobbler.session = session
check('a playback left alone (a clip, another account\'s login) still owns Kodi\'s own watched bump', scrobbler.is_open('movie', 1) and not scrobbler.is_open('movie', 2))

# --- a 400 without Simkl's error code (a proxy page, an empty body) is not a refusal ---
q = fresh_queue()
q.auth = PassAuth()
q.add_mark(ITEM)
entry = q.entries[0]
q.api = types.SimpleNamespace(call=lambda *a, **k: (_ for _ in ()).throw(ApiError(400, '')))
check('refusal: a bodiless 400 keeps the entry for a retry', q._send([entry], '/sync/history', True, 'A') is False and kinds(q) == [h.MARK])
q.api = types.SimpleNamespace(call=lambda *a, **k: (_ for _ in ()).throw(ApiError(400, 'wrong_parameter', 'bad')))
check('refusal: a 400 with Simkl\'s error code drops the single entry', q._send([entry], '/sync/history', True, 'A') is True and q.entries == [])

# --- a file named at the flush respects the tracking switch of its type ---
q = fresh_queue()
q.watch_event({'kind': None, 'key': 'file:x', 'dbid': None, 'title': 'x', 'year': None, 'ids': {}, 'file': r'D:\x.mkv', 'runtime': 0, 'label': 'x.mkv'}, time.time(), False)
q.release(q.entries[0]['id'], toast=False)
playerlib.search_file = lambda api, auth, account, item: playerlib.remote_episode({'simkl': '9'}, 'S', '', 2023, 1, 2, item['file'])
SETTINGS['scrobble_shows'] = 'false'
check('offline: a file named as an episode while show tracking is off is dropped', q._named(q._eligible(), 'A') == [] and q.entries == [])
SETTINGS.pop('scrobble_shows')
playerlib.search_file = real_search

# --- another account's rated stamps do not survive the account change ---
importer._get = lambda path, timeout=30: {}
importer._save({'account': 'B', 'rated': {'movies': '2026-10-02T00:00:00Z'}, 'full_import_at': '2026-10-01T00:00:00Z', 'cursors': {}})
RPC['VideoLibrary.GetMovies'] = {'limits': {'start': 0, 'end': 1, 'total': 1}}
RPC['VideoLibrary.GetEpisodes'] = {'limits': {'start': 0, 'end': 1, 'total': 0}}
importer._run('check', False, False)
check('ratings: the previous account\'s rated stamps are dropped at the account change', not importer._load().get('rated'))

# --- a type whose ratings were too many for one call keeps no stamp, so the next run asks again ---
RPC['VideoLibrary.GetTVShows'] = {'tvshows': [{'tvshowid': 5, 'uniqueid': {'tvdb': '9'}, 'userrating': 0}]}
asked.clear()


def too_many_shows(path, timeout=30):
    asked.append(path)
    if path == '/sync/activities':
        return {'tv_shows': {'rated_at': '2026-10-02T00:00:00Z', 'completed': '2026-10-01T00:00:00Z'}}
    if path.startswith('/sync/all-items/shows?') or path.startswith('/sync/ratings/shows/'):
        raise ApiError(400, 'max_items', 'too many')
    return {}


importer._get = too_many_shows
importer.auth = Auth(None)
importer._save({})
importer._run('reset', False, False)
check('ratings: a type whose ratings were too many for one call gets no stamp, so the next run asks again',
      '/sync/ratings/shows/1,2,3,4,5,6,7,8,9,10' in asked and 'shows' not in (importer._load().get('rated') or {}))
importer.auth = None
RPC.pop('VideoLibrary.GetMovies')
RPC.pop('VideoLibrary.GetEpisodes')
RPC.pop('VideoLibrary.GetTVShows')
importer.last_finish = 0

# --- each context action carries its own item: two actions in one loop turn act on their own items ---
BUILTINS.clear()
focus.notify_service('ctx_rate', {'dbtype': 'movie', 'dbid': 1, 'rating': 7})
focus.notify_service('import_check')
prefix = 'NotifyAll(script.simkl, ctx_rate, '
check('context: the action goes out with its item as base64 JSON, a plain action without',
      BUILTINS[0].startswith(prefix + '{"data": "') and BUILTINS[1] == 'NotifyAll(script.simkl, import_check)')
data = BUILTINS[0][len(prefix):-1]
check('context: the service decodes it', service_module._payload(data) == {'dbtype': 'movie', 'dbid': 1, 'rating': 7} and service_module._payload('') == {} and service_module._payload('{"data": "!!"}') == {})
rated.clear()
service.sender.jobs.clear()
RPC['VideoLibrary.GetMovieDetails'] = {'moviedetails': {'movieid': 1, 'title': 'X', 'year': 2020, 'uniqueid': {'imdb': 'tt1'}, 'file': r'D:\x.mkv', 'runtime': 5400, 'playcount': 1}}
service.onNotification('script.simkl', 'Other.ctx_rate', data)
focus.notify_service('ctx_rate', {'dbtype': 'movie', 'dbid': 1, 'rating': 3})
service.onNotification('script.simkl', 'Other.ctx_rate', BUILTINS[-1][len(prefix):-1])
service._drain()
for job in service.sender.jobs:
    job()
check('context: two actions queued in one turn each act on their own item and rating', [r[1] for r in rated] == [7, 3])
RPC.pop('VideoLibrary.GetMovieDetails')
rated.clear()
service.sender.jobs.clear()
service.history.entries = []

# --- a playback left alone keeps its library identity, so Kodi's own bump at its end is not a mark ---
RPC['Player.GetActivePlayers'] = [{'playerid': 1, 'type': 'video'}]
RPC['Player.GetItem'] = {'item': {'type': 'movie', 'id': 7, 'file': 'https://cdn/x.mp4'}}
SETTINGS['exclude_http'] = 'true'
auth_ok = types.SimpleNamespace(logged_in=lambda: True, authorized=lambda call, account=None: call('t'), nudge=lambda: None)
scrobbler = scr.Scrobbler(api=None, auth=auth_ok, player=FakePlayer(path='https://cdn/x.mp4'), sender=FakeSender(), history=fresh_queue(), marks=FakeMarks())
scrobbler.on_event(('av_started', 1))
check('an excluded library playback (an http .strm) still owns Kodi\'s own watched bump', scrobbler.session.ignored and scrobbler.is_open('movie', 7))
SETTINGS.pop('exclude_http')
RPC.pop('Player.GetActivePlayers')
RPC.pop('Player.GetItem')

# --- a show marked at once can be a thousand episodes and more: nothing is evicted, the requests are chunked ---
ident(token='simkl_at_A', account_id='A')
api = HistoryApi()
q = h.History(api=api, auth=PassAuth())
q.entries, q.unmarked, q.window_unmarks = [], {}, []
for number in range(1, 2501):
    q.add_mark(dict(EPISODE, key='episode:%d' % number, dbid=number, number=number))
sender = FakeSender()
q.due = 0
q.tick(time.time() + 5, sender)
sender.jobs[0]()
check('bulk mark: 2500 episode marks survive the cap and go out in requests of a thousand',
      [sum(len(e['episodes']) for s in body['shows'] for e in s['seasons']) for _, body in api.sent] == [1000, 1000, 500] and q.entries == [])

# --- a completed TV show is applied from Kodi's own episode list: only the recorded rows come from Simkl ---
index = imp.LibraryIndex()
index.movies_read = index.shows_read = True
index.shows[5] = {'kind': 'tvshow', 'dbid': 5, 'userrating': 0}
index.series['tvdb:9'] = [5]
rows = {}
for season, number in ((0, 1), (1, 1), (1, 2), (2, 1)):
    rows[(season, number)] = dict(ROW, kind='episode', dbid=10 * season + number, lastplayed='')
    index.by_coord['5:%d:%d' % (season, number)] = [rows[(season, number)]]
    if season > 0:
        index.episodes_of.setdefault(5, []).append(rows[(season, number)])
job = imp.Job(False)
importer._apply_show(job, index, {'show': {'ids': {'tvdb': '9'}, 'title': 'S'}, 'status': 'completed', 'last_watched_at': '2026-01-05T00:00:00Z',
                                  'seasons': [{'number': 1, 'episodes': [{'number': 1, 'watched_at': '2026-01-01T00:00:00Z'}]}]}, False, 'completed')
written = {row['dbid']: fields for row, fields in job.writes}
check('completed show: the recorded episode keeps its own date, the other regular episodes get the show\'s, specials stay',
      job.episodes_marked == 3 and sorted(written) == [11, 12, 21] and written[11]['lastplayed'] == utils.ts_to_kodi_date(utils.parse_stamp('2026-01-01T00:00:00Z'))
      and written[12]['lastplayed'] == utils.ts_to_kodi_date(utils.parse_stamp('2026-01-05T00:00:00Z')) and 1 not in written)
job = imp.Job(False)
importer._apply_show(job, index, {'show': {'ids': {'tvdb': '9'}, 'title': 'S'}, 'status': 'watching', 'seasons': []}, False, 'watching')
check('completed show: a show in progress without rows marks nothing', job.writes == [] and job.episodes_marked == 0)
index = imp.LibraryIndex()
index.movies_read = index.shows_read = True
index.shows[5] = {'kind': 'tvshow', 'dbid': 5, 'userrating': 0}
index.series['tvdb:9'] = [5]
for season, number, aired in ((1, 1, '2024-03-01'), (1, 2, '2024-03-08'), (1, 3, ''), (2, 1, '2026-03-01'), (2, 2, '2026-01-05'), (2, 3, '2026-01-06'), (2, 4, '2026-01-07')):
    row = dict(ROW, kind='episode', dbid=10 * season + number, key='episode:%d' % (10 * season + number), lastplayed='', firstaired=aired)
    index.by_coord['5:%d:%d' % (season, number)] = [row]
    index.episodes_of.setdefault(5, []).append(row)
index.episodes_of[5].append(dict(ROW, kind='episode', dbid=25, key='episode:25', playcount=1, firstaired='2026-03-01'))
job = imp.Job(False)
importer._apply_show(job, index, {'show': {'ids': {'tvdb': '9'}, 'title': 'S'}, 'status': 'completed', 'last_watched_at': '2026-01-05T00:00:00Z', 'seasons': []}, False, 'completed')
check('completed show: an episode that aired more than a day after the completion date is left alone (a new season, which Simkl set the show back to watching for), undated and same-day ones are filled in, a watched one counts as before',
      sorted(row['dbid'] for row, _ in job.writes) == [11, 12, 13, 22, 23] and job.episodes_marked == 5 and job.already == 1)
job = imp.Job(False)
importer._apply_show(job, index, {'show': {'ids': {'tvdb': '9'}, 'title': 'S'}, 'status': 'completed', 'seasons': [{'number': 2, 'episodes': [{'number': 1, 'watched_at': '2026-03-02T00:00:00Z'}]}]}, False, 'completed')
check('completed show: a recorded watch of such an episode marks it, and without a completion date nothing is held back',
      sorted(row['dbid'] for row, _ in job.writes) == [11, 12, 13, 21, 22, 23, 24])
completed_on = utils.parse_stamp('2026-01-05T00:00:00Z')
check('completed show: the air-date rule reads Kodi\'s date formats and takes garbage, no date or no completion date as not after',
      imp._aired_after('2026-03-01', completed_on) and not imp._aired_after('2026-01-05 00:00:00', completed_on) and not imp._aired_after('garbage', completed_on)
      and not imp._aired_after('', completed_on) and not imp._aired_after('2026-03-01', None))

# --- a title the library holds twice (two sources, 1080p and 4K) is every one of its rows ---
twin = imp.LibraryIndex()
twin.movies_read = twin.shows_read = True
twin.movies['imdb:tt1'] = [dict(ROW), dict(ROW, dbid=2, key='movie:2')]
twin.shows[5] = {'kind': 'tvshow', 'dbid': 5, 'userrating': 0}
twin.shows[6] = {'kind': 'tvshow', 'dbid': 6, 'userrating': 0}
twin.series['tvdb:9'] = [5, 6]
twin.series['imdb:tt9'] = [5]
for tvshowid in (5, 6):
    twin.by_coord['%d:1:1' % tvshowid] = [dict(ROW, kind='episode', dbid=10 * tvshowid + 1, key='episode:%d' % (10 * tvshowid + 1), lastplayed='')]
job = imp.Job(False)
importer._apply(job, twin, 'movies', [{'movie': {'ids': {'imdb': 'tt1'}, 'title': 'X'}, 'status': 'completed', 'last_watched_at': '2026-01-05T00:00:00Z', 'user_rating': 8}])
importer._apply(job, twin, 'shows', [{'show': {'ids': {'tvdb': '9', 'imdb': 'tt9'}, 'title': 'S'}, 'status': 'watching', 'user_rating': 7,
                                     'seasons': [{'number': 1, 'episodes': [{'number': 1, 'watched_at': '2026-01-01T00:00:00Z'}]}]}])
check('twins: both copies of a movie and the episode of both copies of a show are marked, each copy rated, a show reached by two ids once',
      sorted(row['dbid'] for row, _ in job.writes) == [1, 2, 51, 61] and job.movies_marked == 2 and job.episodes_marked == 2 and job.movies_missing == job.episodes_missing == job.shows_missing == 0
      and sorted((row['dbid'], fields['userrating']) for row, fields in job.rating_writes) == [(1, 8), (2, 8), (5, 7), (6, 7)])
check('twins: find_series lists every show with the id it was matched by, find_movie every row', twin.find_series({'tvdb': '9', 'imdb': 'tt9'}) == [(5, 'tvdb'), (6, 'tvdb')]
      and [row['dbid'] for row in twin.find_movie({'imdb': 'tt1'})] == [1, 2] and twin.find_movie({'imdb': 'tt2'}) == [] and twin.find_episode(7, 1, 1) == [])
twin.by_coord['5:1:1'][0]['tvdb'] = '777'
twin.by_tvdb['777'] = [twin.by_coord['5:1:1'][0]]
shows_9 = twin.find_series({'tvdb': '9'})
by_id = imp.Importer._find_episode(twin, shows_9, False, {}, {'number': 1}, {'number': 1, 'ids': {'tvdb_id': 777}})
twin.by_coord['6:1:1'][0]['tvdb'] = '999'
other_id = imp.Importer._find_episode(twin, shows_9, False, {}, {'number': 1}, {'number': 1, 'ids': {'tvdb_id': 777}})
by_coords = imp.Importer._find_episode(twin, shows_9, False, {}, {'number': 1}, {'number': 1, 'ids': {'tvdb_id': 778}})
no_id = imp.Importer._find_episode(twin, shows_9, False, {}, {'number': 1}, {'number': 1})
check('twins: a TVDB episode id hit takes the twin\'s row without ids along and leaves a row carrying another id out; an id Kodi has on no row matches no row carrying another one; without an id from Simkl, the coordinates in every show',
      [row['dbid'] for row in by_id] == [51, 61] and [row['dbid'] for row in other_id] == [51] and by_coords == [] and [row['dbid'] for row in no_id] == [51, 61])
other = imp.LibraryIndex()
other.movies_read = other.shows_read = True
other.shows[7] = {'kind': 'tvshow', 'dbid': 7, 'userrating': 0}
other.series['tvdb:70'] = [7]
for number, tvdb_id in ((1, '101'), (2, '103'), (3, '104')):
    row = dict(ROW, kind='episode', dbid=70 + number, key='episode:%d' % (70 + number), lastplayed='', tvdb=tvdb_id)
    other.by_coord['7:1:%d' % number] = [row]
    other.by_tvdb[tvdb_id] = [row]
shows_70 = other.find_series({'tvdb': '70'})
unknown = imp.Importer._find_episode(other, shows_70, False, {}, {'number': 1}, {'number': 2, 'ids': {'tvdb_id': 102}})
by_id = imp.Importer._find_episode(other, shows_70, False, {}, {'number': 1}, {'number': 3, 'ids': {'tvdb_id': 103}})
other.by_coord['7:1:2'][0]['tvdb'] = ''
unscraped = imp.Importer._find_episode(other, shows_70, False, {}, {'number': 1}, {'number': 2, 'ids': {'tvdb_id': 102}})
check('numbering: in a show Kodi\'s scraper numbers otherwise, Simkl\'s S01E02 with an id on no row marks nothing (Kodi\'s S01E02 is another episode), its S01E03 marks the row carrying that id, a row without ids goes by the numbers',
      unknown == [] and [row['dbid'] for row in by_id] == [72] and [row['dbid'] for row in unscraped] == [72])
RPC['VideoLibrary.GetTVShows'] = {'tvshows': [{'tvshowid': 5, 'uniqueid': {'tvdb': '9'}, 'userrating': 0}]}
RPC['VideoLibrary.GetEpisodes'] = {'episodes': [{'episodeid': 11, 'tvshowid': 5, 'season': 1, 'episode': 1, 'playcount': 0, 'uniqueid': {'tvdb': '55'}, 'firstaired': '2024-03-01'},
                                                {'episodeid': 12, 'tvshowid': 5, 'season': 1, 'episode': 2, 'playcount': 0, 'uniqueid': {'tvdb': '0'}}], 'limits': {'start': 0, 'end': 2, 'total': 2}}
read_index = imp.LibraryIndex()
read_index.read_shows()
check('twins: the index rows keep their own TVDB episode id, none for a missing or zero one, and their air date',
      read_index.by_key['episode:11']['tvdb'] == '55' and read_index.by_key['episode:12']['tvdb'] == '' and list(read_index.by_tvdb) == ['55']
      and read_index.by_key['episode:11']['firstaired'] == '2024-03-01' and read_index.by_key['episode:12']['firstaired'] == '' and 'firstaired' in imp.EPISODE_PROPERTIES)
RPC.pop('VideoLibrary.GetTVShows')
RPC.pop('VideoLibrary.GetEpisodes')

# --- the shows call asks for the recorded episodes only; past the cap, the completed shows come as titles ---
asked.clear()


def capped_shows(path, timeout=30):
    asked.append(path)
    if path.startswith('/sync/all-items/shows?') or (path.startswith('/sync/all-items/shows/completed?') and 'include_all_episodes' in path):
        raise ApiError(400, 'max_items', 'too many')
    if path.startswith('/sync/all-items/shows/completed?'):
        return {'shows': [{'show': {'ids': {'tvdb': '9'}}, 'status': 'completed'}]}
    return {}


importer._get = capped_shows
importer.auth = Auth(None)
job = imp.Job(False)
got = importer._fetch_type(job, 'shows', None)
check('shows: asked with extended=full and the recorded episodes only; past the cap once more without the TVDB episode ids, then the completed shows come as titles',
      asked[0] == '/sync/all-items/shows?extended=full&episode_watched_at=yes&episode_tvdb_id=yes&include_all_episodes=original'
      and asked[1] == '/sync/all-items/shows/completed?extended=full&episode_watched_at=yes&episode_tvdb_id=yes&include_all_episodes=original'
      and asked[2] == '/sync/all-items/shows/completed?extended=full&episode_watched_at=yes&include_all_episodes=original'
      and asked[3] == '/sync/all-items/shows/completed?extended=full&episode_watched_at=yes&episode_tvdb_id=yes' and len(got) == 1 and 'shows' in job.partial)
asked.clear()
importer._get = lambda path, timeout=30: asked.append(path) or {}
importer._fetch_type(job, 'anime', None)
importer._fetch_all(job, '2026-01-01T00:00:00Z')
check('shows: anime and the incremental delta keep the server\'s rows for every episode',
      asked == ['/sync/all-items/anime?extended=full_anime_seasons&episode_watched_at=yes&episode_tvdb_id=yes&include_all_episodes=yes',
                '/sync/all-items?extended=full_anime_seasons&episode_watched_at=yes&episode_tvdb_id=yes&include_all_episodes=yes&date_from=2026-01-01T00%3A00%3A00Z'])
asked.clear()
importer._get = lambda path, timeout=30: asked.append(path) or ((_ for _ in ()).throw(ApiError(400, 'max_items', 'too many')) if path.count('/') == 3 else {})
job = imp.Job(False)
importer._fetch_type(job, 'movies', '2026-01-01T00:00:00Z')
importer._fetch_type(job, 'shows', '2026-01-01T00:00:00Z')
dropped_status = importer._dropped()
check('delta per status: the statuses a title can move to (Plan to watch, dropped) are asked as well, a full run leaves them to the ratings call',
      [p.split('?')[0] for p in asked] == ['/sync/all-items/movies', '/sync/all-items/movies/completed', '/sync/all-items/movies/plantowatch', '/sync/all-items/movies/' + dropped_status,
                                          '/sync/all-items/shows', '/sync/all-items/shows/completed', '/sync/all-items/shows/watching', '/sync/all-items/shows/hold', '/sync/all-items/shows/' + dropped_status, '/sync/all-items/shows/plantowatch']
      and all('date_from=' in p for p in asked))
asked.clear()


def lighter_statuses(path, timeout=30):
    asked.append(path)
    if path.count('/') == 3 or 'episode_tvdb_id' in path:
        raise ApiError(400, 'max_items', 'too many')
    return {'anime': [{'show': {'ids': {'simkl': 8}}, 'status': 'completed', 'seasons': []}], 'shows': [{'show': {'ids': {'simkl': 9}}, 'status': 'watching', 'seasons': []}], 'movies': []}


importer._get = lighter_statuses
job = imp.Job(False)
anime_rows = importer._fetch_type(job, 'anime', None)
anime_asked = list(asked)
asked.clear()
shows_rows = importer._fetch_type(job, 'shows', None)
shows_asked = list(asked)
check('huge histories: a status refused with the TVDB episode ids is asked once more without them and its rows count',
      anime_asked[1:3] == ['/sync/all-items/anime/completed?extended=full_anime_seasons&episode_watched_at=yes&episode_tvdb_id=yes&include_all_episodes=yes',
                          '/sync/all-items/anime/completed?extended=full_anime_seasons&episode_watched_at=yes&include_all_episodes=yes']
      and len(anime_rows) == 4 and '/sync/all-items/shows/watching?extended=full&episode_watched_at=yes&include_all_episodes=original' in shows_asked and len(shows_rows) == 4)
importer._get = lambda path, timeout=30: asked.append(path) or (_ for _ in ()).throw(ApiError(400, 'max_items', 'too many'))
outcomes = []
for kind in ('anime', 'movies'):
    asked.clear()
    try:
        importer._fetch_type(imp.Job(False), kind, None)
        outcomes.append(None)
    except imp.RunError as exc:
        outcomes.append((str(exc), len(asked)))
check('huge histories: an anime status refused twice fails the run after two calls for it, a refused movie status after one (no episodes to shed)',
      outcomes == [('str32244 {0}', 3), ('str32244 {0}', 2)])
importer.auth = None

# --- the cached history: a full run fills it, a run after the library grew applies it without a request ---
slim = imp._slim({'show': {'ids': {'simkl': 9, 'tvdb': '9'}, 'title': 'S', 'year': 2020, 'poster': 'x'}, 'status': 'watching', 'last_watched_at': '2026-01-05T00:00:00Z',
                  'user_rating': None, 'total_episodes_count': 10,
                  'seasons': [{'number': 1, 'episodes': [{'number': 1, 'watched_at': '2026-01-01T00:00:00Z', 'tvdb': {'season': 1, 'episode': 1}, 'ids': {'tvdb_id': 5}, 'title': 'E'}]}]})
check('cache: a row is kept with what the apply steps read, nothing else',
      slim == {'show': {'ids': {'simkl': 9, 'tvdb': '9'}, 'title': 'S'}, 'status': 'watching', 'last_watched_at': '2026-01-05T00:00:00Z',
               'seasons': [{'number': 1, 'episodes': [{'number': 1, 'watched_at': '2026-01-01T00:00:00Z', 'tvdb': {'season': 1, 'episode': 1}, 'ids': {'tvdb_id': 5}}]}]})
writes_seen = []


def library_batch(calls):
    answers = []
    for method, params in calls:
        if method.startswith('VideoLibrary.Get'):
            answers.append({'result': {key: {'playcount': 0, 'lastplayed': '', 'userrating': 0} for key in ('episodedetails', 'moviedetails', 'tvshowdetails')}})
        else:
            writes_seen.append((method, params))
            answers.append({'result': 'OK'})
    return answers


def library(episodes, watched=()):
    RPC['VideoLibrary.GetMovies'] = {'limits': {'start': 0, 'end': 1, 'total': 0}}
    RPC['VideoLibrary.GetTVShows'] = {'tvshows': [{'tvshowid': 5, 'uniqueid': {'tvdb': '9'}, 'userrating': 0}]}
    RPC['VideoLibrary.GetEpisodes'] = {'episodes': [{'episodeid': 10 + n, 'tvshowid': 5, 'season': 1, 'episode': n, 'playcount': 1 if n in watched else 0, 'uniqueid': {}} for n in episodes],
                                       'limits': {'start': 0, 'end': 1, 'total': len(episodes)}}


SHOW_ROW = {'show': {'ids': {'tvdb': '9', 'simkl': 9}, 'title': 'S'}, 'status': 'completed', 'last_watched_at': '2026-01-05T00:00:00Z',
            'seasons': [{'number': 1, 'episodes': [{'number': 1, 'watched_at': '2026-01-01T00:00:00Z'}]}]}


def cached_history(path, timeout=30):
    asked.append(path)
    if path == '/sync/activities':
        return {'tv_shows': {'completed': '2026-10-01T00:00:00Z'}}
    if path.startswith('/sync/all-items/shows?'):
        return {'shows': [dict(SHOW_ROW, extra='dropped by the cache')]}
    return {}


utils.jsonrpc_batch = library_batch
importer._get = cached_history
SETTINGS.pop('continue_watching', None)
library([1])
asked.clear()
importer._save({})
importer._run('reset', False, False)
cached = store.read(store.HISTORY, None)
check('cache: a full run fetches the history per type, applies it and keeps a slim copy',
      [p.split('?')[0] for p in asked] == ['/sync/activities', '/sync/all-items/shows', '/sync/all-items/anime', '/sync/all-items/movies']
      and cached['account'] == 'A' and cached['shows']['9']['status'] == 'completed' and 'extra' not in cached['shows']['9']
      and [w[1]['episodeid'] for w in writes_seen if w[0] == 'VideoLibrary.SetEpisodeDetails'] == [11])
writes_seen.clear()


def growing_history(path, timeout=30):
    asked.append(path)
    if path == '/sync/activities':
        return {'tv_shows': {'completed': '2026-10-01T00:00:00Z'}}
    if path.startswith('/sync/all-items/shows?'):
        # episode 2 arrives while the history is fetched, after the index was read
        library([1, 2])
        return {'shows': [SHOW_ROW]}
    return {}


importer._get = growing_history
library([1])
importer._save({})
importer._run('reset', False, False)
baseline = importer._load().get('library_totals', {}).get('episodes')
first_marks = [w[1]['episodeid'] for w in writes_seen if w[0] == 'VideoLibrary.SetEpisodeDetails']
writes_seen.clear()
library([1, 2], watched=(1,))
importer._run('check', False, False)
check('growth: a title that arrived during a run keeps the baseline at the start\'s count, so the next run sees the growth and marks it from the kept copy',
      baseline == 1 and first_marks == [11] and [w[1]['episodeid'] for w in writes_seen if w[0] == 'VideoLibrary.SetEpisodeDetails'] == [12])
# back to the state the original full run left (one episode, baseline 1) for the checks that follow
importer._get = cached_history
library([1])
importer._save({})
importer._run('reset', False, False)
writes_seen.clear()
asked.clear()
library([1, 2], watched=(1,))
toasts = []
utils.notify = lambda message, *a, **k: toasts.append(message)
importer._run('check', False, False)
utils.notify = real_notify
last_summary = importer._load().get('last_summary', '')
check('cache: after the library grew, one activities call and the new episode takes its mark from the cache',
      asked == ['/sync/activities'] and [w[1]['episodeid'] for w in writes_seen if w[0] == 'VideoLibrary.SetEpisodeDetails'] == [12]
      and last_summary.startswith('str32230'))
check('sync toast: the toast stops before the not-in-the-library counts, which the log and the saved summary keep',
      len(toasts) == 1 and 'str32279' not in toasts[0] and 'str32279' in last_summary and last_summary.startswith(toasts[0]) and toasts[0].startswith('str32230'))

# --- unmark ledger: what the user unmarked in Kodi is not marked again by the sync ---
T = time.time()
job = imp.Job(False)
job.unmarked = {'movie:1': T, 'movie:2': T, 'movie:3': T}
imp.Importer._mark(job, dict(ROW), T - 100, None)
imp.Importer._mark(job, dict(ROW, key='movie:2', dbid=2), None, T + 100)
imp.Importer._mark(job, dict(ROW, key='movie:3', dbid=3), T + 100, None)
check('ledger: an item unmarked in Kodi after Simkl\'s watch stays unwatched (a show-level date does not count), a newer Simkl watch marks it',
      [row['dbid'] for row, _ in job.writes] == [3] and job.kept == 2 and job.rewatched == ['movie:3'])
index = imp.LibraryIndex()
index.movies_read = True
index.by_key = {'movie:1': dict(ROW), 'movie:2': dict(ROW, key='movie:2', dbid=2, playcount=1)}
job.unmarked['episode:9'] = T
job.read_at = T + 1
check('ledger: closed for the items marked now, watched again or gone from a read library part; kept for an unread part',
      sorted(imp.Importer._settled_unmarks(job, index)) == ['movie:2', 'movie:3'])
job.unmarked['movie:2'] = T + 2
check('ledger: an unmark made after the library was read is not settled by that read', sorted(imp.Importer._settled_unmarks(job, index)) == ['movie:3'])
q = h.History(api=None, auth=PassAuth())
q.entries, q.unmarked, q.window_unmarks = [], {'file:D:\\old.mkv': T - 31 * 86400, 'movie:5': T - 31 * 86400}, []
q.add_unmark(dict(EPISODE, key='episode:21', dbid=21), arrived=T)
q.add_unmark(dict(EPISODE, key='episode:22', dbid=22, number=3), arrived=T + 0.5)
q._settle_window(T + 10)
q._eligible()
check('ledger: a bulk unmark is remembered per episode, a library key never ages out, a file key does after 30 days',
      q.entries == [] and set(q.unmarked) == {'movie:5', 'episode:21', 'episode:22'} and q.library_unmarks() == q.unmarked)
q.add_unmark(dict(EPISODE, key='episode:23', dbid=23, number=4), explicit=True)
q.forget_unmarks({'movie:5': time.time(), 'episode:21': time.time(), 'episode:99': time.time()})
q.add_mark(dict(EPISODE, key='episode:22', dbid=22, number=3))
check('ledger: a queued unmark counts until delivered, a mark closes the entry, forgotten keys go',
      set(q.library_unmarks()) == {'episode:23'} and set(q.unmarked) == set())
writes_seen.clear()
asked.clear()
library([1, 2, 3])   # a third episode arrived; meanwhile the user unmarked the first two in Kodi (a bulk, never sent)
ledger.unmarked = {'episode:11': time.time(), 'episode:12': time.time()}
importer._run('check', False, False)
check('ledger: the run marks only the new episode, the two unmarked in Kodi stay unwatched and the summary says so',
      asked == ['/sync/activities'] and [w[1]['episodeid'] for w in writes_seen if w[0] == 'VideoLibrary.SetEpisodeDetails'] == [13]
      and 'str32302' in importer._load().get('last_summary', '') and set(ledger.unmarked) == {'episode:11', 'episode:12'})
writes_seen.clear()
importer._run('reset', False, False)
check('ledger: a reset takes Simkl\'s state again, the unmarked episodes included',
      ledger.unmarked == {} and sorted(w[1]['episodeid'] for w in writes_seen if w[0] == 'VideoLibrary.SetEpisodeDetails') == [11, 12, 13])
for method in ('VideoLibrary.GetMovies', 'VideoLibrary.GetTVShows', 'VideoLibrary.GetEpisodes'):
    RPC.pop(method)
utils.jsonrpc_batch = real_batch
importer.last_finish = 0

# --- a rating of a movie Kodi has unwatched is not sent: Simkl would file the movie as watched ---
rated.clear()
service.sender.jobs.clear()
RPC['VideoLibrary.GetMovieDetails'] = {'moviedetails': {'movieid': 1, 'title': 'X', 'year': 2020, 'uniqueid': {'imdb': 'tt1'}, 'file': r'D:\x.mkv', 'runtime': 5400, 'playcount': 0}}
CONDS['Window.IsVisible(movieinformation)'] = True
LABELS.update({'ListItem.DBTYPE': 'movie', 'ListItem.DBID': '1', 'ListItem.UserRating': ''})
service._watch_rating()
LABELS['ListItem.UserRating'] = '7'
service._watch_rating()
CONDS['Window.IsVisible(movieinformation)'] = False
service._watch_rating()
service._context('ctx_rate', {'dbtype': 'movie', 'dbid': 1, 'rating': 7})
for job in service.sender.jobs:
    job()
check('ratings: an unwatched library movie is not rated on Simkl, from the information dialog or the Simkl menu', rated == [] and service.sender.jobs == [])
service._context('ctx_rate', {'dbtype': 'movie', 'dbid': 1, 'rating': 0})
RPC['VideoLibrary.GetMovieDetails']['moviedetails']['playcount'] = 1
service._context('ctx_rate', {'dbtype': 'movie', 'dbid': 1, 'rating': 7})
for job in service.sender.jobs:
    job()
check('ratings: removing its rating still goes, and so does a rating once Kodi has it watched', [r[1] for r in rated] == [0, 7])
RPC.pop('VideoLibrary.GetMovieDetails')
LABELS.clear()
LABELS['System.BuildVersion'] = '21.2'
rated.clear()
service.sender.jobs.clear()
service.history.entries = []

# --- shutdown: a request in flight gives up at its next chunk once the service is leaving ---
urllib.request.urlopen = lambda req, timeout=30: FakeResponse(200, b'{"ok": 1}')
api = apimod.Api()
api.abort = True
try:
    api.request('GET', '/sync/activities', token='simkl_at_A')
    check('shutdown: a request read after the abort flag ends as a transport failure', False)
except ApiError as exc:
    check('shutdown: a request read after the abort flag ends as a transport failure', exc.transport and exc.message == 'shutdown')
api.abort = False
check('shutdown: without the flag the same answer reads as before', api.request('GET', '/sync/activities', token='simkl_at_A') == (200, {'ok': 1}))
urllib.request.urlopen = real_urlopen

# --- the ledger follows Kodi's own watched state whatever the switches say ---
import threading  # noqa: E402
from resources.lib import marks as marksmod  # noqa: E402

T2 = time.time()
q = fresh_queue()
m = marksmod.Marks(types.SimpleNamespace(logged_in=lambda: True), q, lambda kind, dbid: False)
RPC['VideoLibrary.GetEpisodeDetails'] = {'episodedetails': {'episodeid': 31, 'tvshowid': 5, 'season': 1, 'episode': 1, 'file': r'D:\s\e1.mkv', 'uniqueid': {}}}
RPC['VideoLibrary.GetMovieDetails'] = {'moviedetails': {'movieid': 33, 'title': 'M', 'file': r'D:\m.mkv', 'uniqueid': {}}}
SETTINGS['sync_marks'] = 'false'
m.on_update(json.dumps({'item': {'type': 'episode', 'id': 31}, 'playcount': 0}), arrived=T2)
m.on_update(json.dumps({'item': {'type': 'episode', 'id': 32}, 'playcount': 0, 'transaction': True}), arrived=T2)
m.on_update(json.dumps({'item': {'type': 'movie', 'id': 33}, 'playcount': 0}), arrived=T2)
m.on_update(json.dumps({'item': {'type': 'movie', 'id': 33}, 'playcount': 1}), arrived=T2 + 1)
SETTINGS.pop('sync_marks')
RPC.pop('VideoLibrary.GetEpisodeDetails')
RPC.pop('VideoLibrary.GetMovieDetails')
check('ledger: an unmark made with the marks switch off is remembered, a scan\'s own update is not, a mark closes the entry',
      q.entries == [] and q.unmarked == {'episode:31': T2})

# --- a rating delta updates the kept copy of the history: a later cache run must not write the old rating back ---
job = imp.Job(False)
cache = {'movies': {'7': {'movie': {'ids': {'simkl': 7, 'imdb': 'tt1'}}, 'status': 'completed', 'user_rating': 6}}, 'shows': {}, 'anime': {}}
importer._get = lambda path, timeout=30: {'movies': [{'movie': {'ids': {'simkl': 7, 'imdb': 'tt1'}}, 'user_rating': 9}]}
importer._changed_ratings(job, index_with_movie(), {'rated': {'movies': '2026-10-01T00:00:00Z'}}, {'movies': '2026-10-02T00:00:00Z', 'shows': '', 'anime': ''}, cache)
check('ratings: a rating delta updates the kept copy too', cache['movies']['7']['user_rating'] == 9 and store.read(store.HISTORY, None) == cache and job.rating_writes[0][1] == {'userrating': 9})
importer._get = lambda path, timeout=30: PAUSE

# --- only a write that changes the play count is guarded: Kodi announces nothing a mark could be taken for otherwise ---
class RecordingMarks:
    def __init__(self):
        self.guards = []

    def guard(self, kind, dbid, seconds=0):
        self.guards.append((kind, dbid))


guarding = RecordingMarks()
importer.marks = guarding
utils.jsonrpc_batch = library_batch
job = imp.Job(False)
importer._write(job, [(dict(ROW), {'playcount': 1, 'lastplayed': '2026-01-01 00:00:00'}),
                      (dict(ROW, key='movie:2', dbid=2), {'resume': {'position': 600, 'total': 7200}, 'lastplayed': '2026-01-01 00:00:00'}),
                      (dict(ROW, key='movie:3', dbid=3), {'lastplayed': '2026-01-01 00:00:00'})])
check('guard: a resume or date-only write sets no guard, a mark does', guarding.guards == [('movie', 1)] and job.write_failures == 0)
utils.jsonrpc_batch = real_batch
importer.marks = FakeMarks()

# --- shutdown: the sender leaves with a job running or a backlog waiting; the loop's exit path runs on an injected exception ---
sender = service_module.Sender()
started = threading.Event()


def slow():
    started.set()
    time.sleep(0.6)


sender.submit(slow)
sender.submit(slow)
started.wait(1)
sender.stop(0.1)
sender.thread.join(2)
check('shutdown: the sender leaves after the running job and leaves the backlog behind', sender.stopping and not sender.thread.is_alive())
ident(token='')
svc = service_module.SimklService()
svc.player = types.SimpleNamespace(isPlayingVideo=lambda: False)
svc._drain = lambda: (_ for _ in ()).throw(SystemExit())
svc.scrobbler.close_all = lambda: (_ for _ in ()).throw(SystemExit())
svc.history.entries = [svc.history._new(h.MARK, ITEM)]
svc.history.dirty = True
try:
    svc.run()
    check('shutdown: an exception out of the loop still stops the sender and sets the abort flag', False)
except SystemExit:
    svc.sender.thread.join(2)
    check('shutdown: an exception out of the loop, and another landing in the close of the playback, still save the queue, stop the sender and set the abort flag',
          svc.api.abort and not svc.sender.thread.is_alive() and [e['key'] for e in store.read(store.QUEUE, {}).get('entries', [])] == ['movie:1'])
svc.history.entries = []
svc.history._save()
real_wait, service_module.SHUTDOWN_WAIT = service_module.SHUTDOWN_WAIT, 0.2
svc = service_module.SimklService()
svc.player = types.SimpleNamespace(isPlayingVideo=lambda: False)
svc._drain = lambda: (_ for _ in ()).throw(SystemExit())
svc.sender.submit(lambda: time.sleep(1.0))
started_at = time.time()
try:
    svc.run()
except SystemExit:
    pass
took = time.time() - started_at
service_module.SHUTDOWN_WAIT = real_wait
check('shutdown: with a sender job blocking longer than the wait the stop flags are set at the wait, which is inside Kodi\'s 5 s',
      took < 0.2 + 0.5 and svc.api.abort and svc.sender.stopping and real_wait < 5)
sender = service_module.Sender()
sender.stopping = True
sender.thread.join(2)
check('shutdown: a sender told to stop leaves on its own within a second, with no sentinel in its queue', not sender.thread.is_alive())

# --- shutdown: ratings recorded while the sender is held by a slow job are on disk after the stop ---
real_wait, service_module.SHUTDOWN_WAIT = service_module.SHUTDOWN_WAIT, 0.2
ident(token='simkl_at_A', account_id='A')
svc = service_module.SimklService()
svc.history.entries = []
svc.player = types.SimpleNamespace(isPlayingVideo=lambda: False)
svc._drain = lambda: (_ for _ in ()).throw(SystemExit())
svc._refresh_account = lambda: None
RPC['VideoLibrary.GetMovieDetails'] = {'moviedetails': {'movieid': 1, 'title': 'X', 'year': 2020, 'uniqueid': {'imdb': 'tt1'}, 'file': r'D:\x.mkv', 'runtime': 5400, 'playcount': 1}}
RPC['VideoLibrary.GetTVShowDetails'] = {'tvshowdetails': {'tvshowid': 78, 'title': 'S', 'uniqueid': {'tvdb': '9'}, 'userrating': 0}}
svc.sender.submit(lambda: time.sleep(1.0))
time.sleep(0.05)
svc._context('ctx_rate', {'dbtype': 'movie', 'dbid': 1, 'rating': 6})
pick_before, PICK[0] = PICK[0], 3
svc._after_watch(dict(ITEM, key='movie:2', dbid=2, ids={'imdb': 'tt2'}), 'A')
for _ in range(50):
    if len(svc.history.entries) >= 2:
        break
    time.sleep(0.05)
CONDS['Window.IsVisible(movieinformation)'] = True
LABELS.update({'ListItem.DBTYPE': 'tvshow', 'ListItem.DBID': '78', 'ListItem.UserRating': '0'})
svc._watch_rating()
LABELS['ListItem.UserRating'] = '7'
svc._watch_rating()
try:
    svc.run()
except SystemExit:
    pass
service_module.SHUTDOWN_WAIT = real_wait
on_disk = store.read(store.QUEUE, {}).get('entries', [])
check('shutdown: a rating from the Simkl menu, one from the prompt and one in an information dialog still open at the stop are in queue.json though the sender never got free',
      sorted((e['item']['bucket'], e['rating']) for e in on_disk if e['kind'] == h.RATE) == [('movies', 6), ('movies', 8), ('shows', 7)] and svc.api.abort)
CONDS['Window.IsVisible(movieinformation)'] = False
LABELS.clear()
LABELS['System.BuildVersion'] = '21.2'
RPC.pop('VideoLibrary.GetMovieDetails')
RPC.pop('VideoLibrary.GetTVShowDetails')
PICK[0] = pick_before
svc.history.entries = []
svc.history._save()

# --- a login whose identity file cannot be written is not taken ---
ident(token='legacyV1', refresh_token='', account_id='12345', account_name='Old')
api = LoginApi(False)
api.dialog = FakeDialog()
real_store_write = store.write


def failing_write(name, data):
    if name == store.IDENTITY:
        raise OSError('disk full')
    return real_store_write(name, data)


store.write = failing_write
done = []
Auth(api)._poll(api.dialog, 'device', 5, 900, lambda previous, current: done.append((previous, current)))
store.write = real_store_write
check('login: an identity file that cannot be written keeps the old login in memory, reports no success and revokes the grant',
      settings.token() == 'legacyV1' and settings.account_id() == '12345' and done == [] and '/oauth2/revoke' in api.paths)

# --- no renewal ahead of a 401; the 401 renews once and the call is repeated ---
ident(token='simkl_at_A', refresh_token='rA', account_id='A', token_expires=str(int(time.time()) - 10))


class ExpiredApi:
    def __init__(self):
        self.paths = []
        self.calls = 0

    def request(self, method, path, body=None, token=None, timeout=30, client_id=None):
        self.paths.append(path)
        return 200, {'access_token': 'simkl_at_A2', 'refresh_token': 'rA', 'expires_in': 604800}

    def call(self, method, path, body=None, token=None, timeout=30):
        self.calls += 1
        if token == 'simkl_at_A':
            raise ApiError(401, 'unauthorized', '')
        return {'ok': token}


api = ExpiredApi()
answer = Auth(api).authorized(lambda token: api.call('GET', '/x', token=token), 'A')
check('auth: an expired stamp renews nothing by itself; the 401 renews once and the call is repeated with the new token',
      answer == {'ok': 'simkl_at_A2'} and api.paths == ['/oauth2/token'] and api.calls == 2 and settings.token() == 'simkl_at_A2')


class FailingRenewalApi:
    """The call gets a 401; the renewal fails the given way."""
    def __init__(self, status, error):
        self.status, self.error = status, error

    def request(self, method, path, body=None, token=None, timeout=30, client_id=None):
        if self.status == 0:
            raise ApiError(0, 'transport')
        return self.status, {'error': self.error}

    def call(self, method, path, body=None, token=None, timeout=30):
        raise ApiError(401, 'unauthorized', '')


outcomes = []
for status, error in ((0, 'transport'), (500, ''), (400, 'invalid_grant')):
    ident(token='simkl_at_A', refresh_token='rA', account_id='A')
    api = FailingRenewalApi(status, error)
    try:
        Auth(api).authorized(lambda token: api.call('GET', '/x', token=token), 'A')
        outcomes.append((None, settings.token()))
    except InvalidToken:
        outcomes.append(('InvalidToken', settings.token()))
    except ApiError as exc:
        outcomes.append((exc.status, settings.token()))
check('auth: a renewal that fails by transport or a server error surfaces as that failure (the call is retried later, the login stays); one refused for good logs out',
      outcomes == [(0, 'simkl_at_A'), (500, 'simkl_at_A'), ('InvalidToken', '')])
ident(token='simkl_at_A', refresh_token='rA', account_id='A')


# --- the anime record is public: asked without the bearer ---
class PublicApi:
    def __init__(self):
        self.tokens = []

    def call(self, method, path, body=None, token=None, timeout=30):
        self.tokens.append(token)
        return {'anime_type': 'movie'}


api = PublicApi()
check('file search: /anime/{id} goes out without the bearer', playerlib._anime_movie(api, 5) is True and api.tokens == [None])

# --- writes a second apart; a refused batch is bisected ---
apimod.POST_GAP = 0.2
api = apimod.Api()
t0 = time.monotonic()
for _ in range(3):
    with api._post_slot():
        pass
check('api: writes are spaced by the gap', time.monotonic() - t0 >= 0.4)
api = apimod.Api()
slot_times = []


def slow_urlopen(req, timeout=30):
    started = time.monotonic()
    time.sleep(0.3)
    slot_times.append((started, time.monotonic()))
    return FakeResponse(200, b'{"ok": 1}')


urllib.request.urlopen = slow_urlopen
posters = [threading.Thread(target=lambda: api.request('POST', '/sync/history', {'movies': []}, token='simkl_at_A')) for _ in range(2)]
for poster in posters:
    poster.start()
for poster in posters:
    poster.join(5)
urllib.request.urlopen = real_urlopen
slot_times.sort()
check('api: a second write on another thread starts the gap after the first one was answered, not after it was decided',
      len(slot_times) == 2 and slot_times[1][0] >= slot_times[0][1] + 0.2 - 0.01)
apimod.POST_GAP = 0.0


class RefusingOneApi:
    def __init__(self):
        self.sent = []

    def call(self, method, path, body=None, token=None, timeout=30):
        self.sent.append(body)
        if any(movie['ids'].get('imdb') == 'bad' for movie in body.get('movies') or []):
            raise ApiError(400, 'invalid_request', 'bad item')
        return {'added': {'movies': len(body.get('movies') or [])}, 'not_found': {}}


ident(token='simkl_at_A', account_id='A')
api = RefusingOneApi()
q = h.History(api=api, auth=PassAuth())
q.entries, q.unmarked, q.window_unmarks = [], {}, []
for n in range(1, 9):
    q.add_mark(dict(ITEM, key='movie:%d' % n, dbid=n, ids={'imdb': 'bad' if n == 6 else 'tt%d' % n}))
sender = FakeSender()
q.due = 0
q.tick(time.time() + 5, sender)
sender.jobs[0]()
check('refusal: a refused batch is bisected in the same flush, good halves go whole, the bad entry alone is dropped',
      [len(body['movies']) for body in api.sent] == [8, 4, 4, 2, 1, 1, 2] and q.entries == [] and q.due is None)

# --- two id-less shows stay two entries ---
body = h.history_request([{'item': dict(EPISODE, key='e:1', ids={}, showtitle='A', episode_ids={'tvdb': '11'}), 'time': T2, 'watched_at': None},
                          {'item': dict(EPISODE, key='e:2', ids={}, showtitle='B', episode_ids={'tvdb': '22'}), 'time': T2, 'watched_at': None}], True)
check('history request: two id-less shows stay two entries (one key would pool them under the first title)', [s['title'] for s in body['shows']] == ['A', 'B'])

# --- seeks add up to the far mark before a start goes out ---
sender = FakeSender()
scrobbler = scr.Scrobbler(api=None, auth=passing, player=FakePlayer(position=100.0, total=3600.0), sender=sender, history=fresh_queue(), marks=FakeMarks())
session = scr.Session(1)
session.item = dict(ITEM)
session.started = True
session.total = 3600.0
scrobbler.session = session
scrobbler.on_event(('seek', 1, 10000))
scrobbler.on_event(('seek', 1, -15000))
scrobbler.tick(time.time() + 5)
short = len(sender.jobs)
scrobbler.on_event(('seek', 1, 5000))
scrobbler.tick(time.time() + 5)
far = len(sender.jobs)
scrobbler.on_event(('seek', 1, 3000))
scrobbler.on_event(('seek', 1, None))
scrobbler.tick(time.time() + 5)
check('seek: skips send nothing until they add up to 30 s, a chapter jump counts as far, a start settles the sum',
      short == 0 and far == 1 and len(sender.jobs) == 2 and session.seeked == 0)

# --- one toast per flush for what Simkl recorded, decided per entry, none over a playing video ---
toasts = []
utils.notify = lambda message, *a, **k: toasts.append(message)
q = fresh_queue()
watch = lambda n: {'id': 'w%d' % n, 'kind': h.WATCH, 'key': 'movie:%d' % n, 'item': dict(ITEM, key='movie:%d' % n, dbid=n, ids={'imdb': 'tt%d' % n}, label='M%d' % n), 'time': T2}  # noqa: E731
ep = lambda n, season, show='1': {'id': 'e%d' % n, 'kind': h.WATCH, 'key': 'episode:%d' % n, 'item': dict(EPISODE, key='episode:%d' % n, dbid=n, season=season, number=n, ids={'tvdb': show}, label='E%d' % n), 'time': T2}  # noqa: E731
q.reported = q._report([watch(1), watch(2), watch(3)], {'added': {'movies': 2}, 'not_found': {'movies': [{'ids': {'imdb': 'tt3'}}]}}, '/sync/history')
q._toast_recorded()
q.reported = q._report([watch(1)], {'added': {'movies': 1}, 'not_found': {}}, '/sync/history')
q._toast_recorded()
q.reported = q._report([watch(1)], {'added': {'movies': 1}, 'not_found': {}}, '/sync/history') + q._report([watch(2)], {'added': {'movies': 1}, 'not_found': {}}, '/sync/history?allow_rewatch=yes')
q._toast_recorded()
unfound = {'shows': [{'ids': {'tvdb': '2'}}], 'episodes': [{'ids': {'tvdb': '1'}, 'seasons': [{'number': 1, 'episodes': [{'number': 5}]}]}]}
q.reported = q._report([ep(5, 1), ep(6, 1), ep(7, 2), ep(8, 1, '2')], {'added': {'episodes': 2}, 'not_found': unfound}, '/sync/history')
q._toast_recorded()
CONDS['Player.HasVideo'] = True
q.reported = q._report([watch(1), watch(2)], {'added': {'movies': 2}, 'not_found': {}}, '/sync/history')
q._toast_recorded()
CONDS['Player.HasVideo'] = False
utils.notify = real_notify
check('toasts: one per flush for what Simkl recorded, by entry (an unknown movie, an unknown show, an unknown episode by season and number), two requests one toast, a single watch by name, none over a playing video',
      toasts == ['str32303 2', 'str32028 M1', 'str32303 2', 'str32303 2'] and q.reported == [])

# --- no watermark from the box clock: without an activity stamp the next delta asks for everything ---
calls = []


def no_all(path, timeout=30):
    calls.append(path)
    if path == '/sync/activities':
        return {'all': '2026-10-05T00:00:00Z', 'tv_shows': {'completed': '2026-10-05T00:00:00Z'}} if len(calls) > 1 else {}
    if path.startswith('/sync/all-items'):
        return {'shows': [], 'anime': [], 'movies': []}
    return {}


utils.jsonrpc_batch = library_batch
importer._get = no_all
library([])
importer._save({})
importer._run('check', False, False)
first_stamp = importer._load().get('synced_at')
importer._run('check', False, False)
check('watermark: no activity stamp -> none saved, the next delta sends no date_from, then the server stamp is kept',
      first_stamp == '' and any(p.startswith('/sync/all-items?') and 'date_from' not in p for p in calls) and importer._load().get('synced_at') == '2026-10-05T00:00:00Z')
for method in ('VideoLibrary.GetMovies', 'VideoLibrary.GetTVShows', 'VideoLibrary.GetEpisodes'):
    RPC.pop(method)
utils.jsonrpc_batch = real_batch
importer._get = lambda path, timeout=30: PAUSE

# --- the minimum length applies to add-on streams and unnamed files, never to an identified library item ---
SETTINGS['min-length'] = '5'
scrobbler = scr.Scrobbler(api=None, auth=passing, player=FakePlayer(), sender=FakeSender(), history=fresh_queue(), marks=FakeMarks())
session = scr.Session(1)
session.item = dict(EPISODE, dbid=2, file=r'D:\x.mkv')
session.library = True
session.total = 180.0
session.created = time.time() - 5
scrobbler._maybe_start(session)
stream = scr.Session(2)
stream.item = playerlib.remote_episode({'tvdb': '1'}, 'S', 'E', None, 1, 2, 'https://cdn/x.mp4', 180)
stream.total = 180.0
stream.created = time.time() - 5
scrobbler._maybe_start(stream)
SETTINGS.pop('min-length')
check('minimum length: a 3-minute library episode with ids is tracked, a 3-minute add-on stream is not, the manual mark of a short episode is eligible',
      session.started and not session.ignored and stream.ignored and marksmod.Marks._eligible(dict(EPISODE, runtime=120, file=r'D:\x.mkv')))

# --- a multi-episode file: Kodi keeps one play state per file, Simkl hears of every episode in it ---
RPC['VideoLibrary.GetTVShowDetails'] = {'tvshowdetails': {'tvshowid': 77, 'title': 'Two', 'uniqueid': {'tvdb': '70'}}}
RPC['VideoLibrary.GetEpisodes'] = {'episodes': [{'episodeid': 701, 'tvshowid': 77, 'season': 1, 'episode': 1, 'file': r'D:\Two\S01E01E02.mkv', 'uniqueid': {'tvdb': '7001'}},
                                                {'episodeid': 702, 'tvshowid': 77, 'season': 1, 'episode': 2, 'file': r'D:\Two\S01E01E02.mkv', 'uniqueid': {'tvdb': '7002'}},
                                                {'episodeid': 703, 'tvshowid': 77, 'season': 1, 'episode': 3, 'file': r'D:\Two\S01E03.mkv', 'uniqueid': {'tvdb': '7003'}}]}
played = playerlib.episode_item(RPC['VideoLibrary.GetEpisodes']['episodes'][0], RPC['VideoLibrary.GetTVShowDetails']['tvshowdetails'])
episode_calls = []
real_jsonrpc = utils.jsonrpc
utils.jsonrpc = lambda method, params=None: (episode_calls.append((method, params)) if method == 'VideoLibrary.GetEpisodes' else None) or real_jsonrpc(method, params)
siblings = playerlib.file_siblings(played)
single = playerlib.file_siblings(playerlib.episode_item(RPC['VideoLibrary.GetEpisodes']['episodes'][2], RPC['VideoLibrary.GetTVShowDetails']['tvshowdetails']))
utils.jsonrpc = real_jsonrpc
check('multi-episode file: the other episode of the file is found through a filter on its name; a plain name asks nothing',
      [s['dbid'] for s in siblings] == [702] and single == [] and len(episode_calls) == 1 and episode_calls[0][1]['filter'] == {'field': 'filename', 'operator': 'is', 'value': 'S01E01E02.mkv'})
check('multi-episode file: the name shapes Kodi reads as several episodes (a second number right after the first, or the same pattern again later in the name)',
      all(playerlib.MULTI_EPISODE.search(name) for name in ('Show.S01E01E02.mkv', 'Show - S01E01-02.mkv', 'Show.1x01x02.mkv', 'Show.E01E02.mkv', 'Show.S01E01E02E03.mkv', 'show s01e01-e02 720p.mkv',
                                                             'Show.S01E01.S01E02.mkv', 'Show - 1x01 - 1x02.mkv', 'One Piece - S01E1071E1072.mkv', 'Show.E01.E02.mkv', 'Show.S2024E01E02.mkv'))
      and not any(playerlib.MULTI_EPISODE.search(name) for name in ('Show.S01E01.1080p.mkv', 'Show.S01E01.mkv', 'Show - 1x01.mkv', 'Show.E01.x264.mkv', 'Show S01E01 - E02.mkv', 'Show.S01E01.1920x1080.mkv', 'Show.S01E01.x265.mkv')))
for row in RPC['VideoLibrary.GetEpisodes']['episodes'][:2]:
    row['file'] = r'D:\Two\Show - 1x01 - 1x02.mkv'
episode_calls.clear()
utils.jsonrpc = lambda method, params=None: (episode_calls.append((method, params)) if method == 'VideoLibrary.GetEpisodes' else None) or real_jsonrpc(method, params)
twin_shape = playerlib.file_siblings(playerlib.episode_item(RPC['VideoLibrary.GetEpisodes']['episodes'][0], RPC['VideoLibrary.GetTVShowDetails']['tvshowdetails']))
utils.jsonrpc = real_jsonrpc
check('multi-episode file: a Sonarr-style name (the pattern repeated) is asked about by its bare name and gives the other episode',
      [s['dbid'] for s in twin_shape] == [702] and episode_calls[0][1]['filter']['value'] == 'Show - 1x01 - 1x02.mkv')
stack = 'stack://smb://nas/tv/Show.S01E01E02.cd1.avi , smb://nas/tv/Show.S01E01E02.cd2.avi'
filters = []
for path in (stack, 'smb://nas/tv/Show.S01E01E02.mkv'):
    for row in RPC['VideoLibrary.GetEpisodes']['episodes'][:2]:
        row['file'] = path
    episode_calls.clear()
    utils.jsonrpc = lambda method, params=None: (episode_calls.append((method, params)) if method == 'VideoLibrary.GetEpisodes' else None) or real_jsonrpc(method, params)
    found = playerlib.file_siblings(playerlib.episode_item(RPC['VideoLibrary.GetEpisodes']['episodes'][0], RPC['VideoLibrary.GetTVShowDetails']['tvshowdetails']))
    utils.jsonrpc = real_jsonrpc
    filters.append(([s['dbid'] for s in found], episode_calls[0][1]['filter']))
bare = {'field': 'filename', 'operator': 'is', 'value': 'Show.S01E01E02.cd2.avi'}
check('multi-episode file: a stacked file (Kodi stores its whole path as the file name) is asked for by both its bare name and its path, a share file by its bare name alone',
      filters == [([702], {'or': [bare, dict(bare, value=stack)]}), ([702], {'field': 'filename', 'operator': 'is', 'value': 'Show.S01E01E02.mkv'})])
for row in RPC['VideoLibrary.GetEpisodes']['episodes'][:2]:
    row['file'] = r'D:\Two\S01E01E02.mkv'
q = fresh_queue()
scrobbler = scr.Scrobbler(api=None, auth=passing, player=FakePlayer(position=3500.0, total=3600.0), sender=FakeSender(), history=q, marks=FakeMarks())
session = scr.Session(1)
session.item = played
session.siblings = siblings
session.started = True
session.total = 3600.0
session.position = 3500.0
scrobbler.session = session
scrobbler.on_event(('ended', 1))
check('multi-episode file: at the threshold the played episode is written ahead for its stop, the other one is queued for the flush',
      kinds(q) == [h.WATCH, h.WATCH] and [e['key'] for e in q.entries] == ['episode:701', 'episode:702'] and [e['inflight'] for e in q.entries] == [True, False] and q.due is not None)
RPC['VideoLibrary.GetEpisodeDetails'] = {'episodedetails': dict(RPC['VideoLibrary.GetEpisodes']['episodes'][0], playcount=1, lastplayed='2026-01-01 10:00:00')}
q = fresh_queue()
m = marksmod.Marks(types.SimpleNamespace(logged_in=lambda: True), q, lambda kind, dbid: False)
m.on_update(json.dumps({'item': {'type': 'episode', 'id': 701}, 'playcount': 1}), arrived=T2)
check('multi-episode file: a mark on one episode queues both', [e['key'] for e in q.entries] == ['episode:701', 'episode:702'] and kinds(q) == [h.MARK, h.MARK])
q = fresh_queue()
m = marksmod.Marks(types.SimpleNamespace(logged_in=lambda: True), q, lambda kind, dbid: False)
m.on_update(json.dumps({'item': {'type': 'episode', 'id': 701}, 'playcount': 0}), arrived=T2)
q._settle_window(T2 + 10)
check('multi-episode file: an unmark on one is remembered for both and, settled as a single, sent for both',
      set(q.unmarked) == {'episode:701', 'episode:702'} and sorted(e['key'] for e in q.entries) == ['episode:701', 'episode:702']
      and kinds(q) == [h.UNMARK, h.UNMARK] and all('window' not in e and 'siblings' not in e for e in q.entries))
q = fresh_queue()
m = marksmod.Marks(types.SimpleNamespace(logged_in=lambda: True), q, lambda kind, dbid: False)
for n, offset in ((701, 0.0), (702, 0.1), (703, 0.2)):
    RPC['VideoLibrary.GetEpisodeDetails'] = {'episodedetails': dict(RPC['VideoLibrary.GetEpisodes']['episodes'][n - 701], playcount=0)}
    m.on_update(json.dumps({'item': {'type': 'episode', 'id': n}, 'playcount': 0}), arrived=T2 + offset)
q._settle_window(T2 + 10)
check('multi-episode file: a season unmark announcing its episodes in a burst stays a bulk, the siblings do not leak out',
      q.entries == [] and set(q.unmarked) == {'episode:701', 'episode:702', 'episode:703'})
for method in ('VideoLibrary.GetTVShowDetails', 'VideoLibrary.GetEpisodes', 'VideoLibrary.GetEpisodeDetails'):
    RPC.pop(method)


# --- ratings are queued: the latest wins, Simkl's not_found is named, the sync leaves a pending title's Kodi rating alone ---
class RatingsApi:
    def __init__(self):
        self.sent = []

    def call(self, method, path, body=None, token=None, timeout=30):
        self.sent.append((path, body))
        return {'added': {'movies': 1}, 'not_found': {'movies': [{'ids': {'imdb': 'tt404'}, 'rating': 7}]}}


ident(token='simkl_at_A', account_id='A')
api = RatingsApi()
q = h.History(api=api, auth=PassAuth())
q.entries, q.unmarked, q.window_unmarks = [], {}, []
q.add_rating(('movies', {'imdb': 'tt1'}, 'X'), 6)
q.add_rating(('movies', {'imdb': 'tt1'}, 'X'), 8)
q.add_rating(('movies', {'imdb': 'tt404'}, 'Gone'), 7)
q.add_rating(('shows', {'tvdb': '9'}, 'S'), 0)
pending = q.pending_ratings()
toasts = []
utils.notify = lambda message, *a, **k: toasts.append(message)
sender = FakeSender()
q.due = 0
q.tick(time.time() + 5, sender)
sender.jobs[0]()
utils.notify = real_notify
check('ratings queue: the latest rating of a title wins, removals and ratings go in one request each, Simkl\'s not_found is named',
      pending == [{'imdb': 'tt1'}, {'imdb': 'tt404'}, {'tvdb': '9'}] and [p for p, _ in api.sent] == ['/sync/ratings/remove', '/sync/ratings']
      and api.sent[0][1] == {'shows': [{'ids': {'tvdb': '9'}}]} and api.sent[1][1] == {'movies': [{'ids': {'imdb': 'tt1'}, 'rating': 8}, {'ids': {'imdb': 'tt404'}, 'rating': 7}]}
      and q.entries == [] and toasts == ['str32263 Gone'])
job = imp.Job(False)
job.pending_ratings = [{'imdb': 'tt1'}]
imp.Importer._rate(job, dict(ROW, userrating=5), {'movie': {'ids': {'imdb': 'tt1', 'simkl': 7}}, 'user_rating': 9})
imp.Importer._rate(job, dict(ROW, key='movie:2', dbid=2, userrating=5), {'movie': {'ids': {'imdb': 'tt2'}}, 'user_rating': 9})
check('ratings queue: the sync leaves the Kodi rating of a title with a pending rating alone', [r['dbid'] for r, _ in job.rating_writes] == [2])


# --- a scheduled run holds its library writes while a video plays unpaused; a manual run does not ---
class FlippingMonitor:
    def __init__(self):
        self.waits = 0

    def waitForAbort(self, seconds):
        self.waits += 1
        CONDS['Player.HasVideo + !Player.Paused'] = False
        return False

    def abortRequested(self):
        return False


CONDS['Player.HasVideo + !Player.Paused'] = True
importer.monitor = FlippingMonitor()
importer._breathe(True)
waited = importer.monitor.waits
CONDS['Player.HasVideo + !Player.Paused'] = True
importer._breathe(False)
check('scheduled run: library writes wait while a video plays unpaused, a manual run does not wait', waited == 1 and importer.monitor.waits == 1)
CONDS['Player.HasVideo + !Player.Paused'] = True
importer.monitor = FlippingMonitor()
batch_after_waits = []
saved_batch = utils.jsonrpc_batch
utils.jsonrpc_batch = lambda calls: batch_after_waits.append(importer.monitor.waits) or library_batch(calls)
job = imp.Job(False)
job.scheduled = True
importer._write(job, [(dict(ROW), {'playcount': 1, 'lastplayed': '2026-01-01 00:00:00'})])
utils.jsonrpc_batch = saved_batch
check('scheduled run: the first chunk of writes waits as well (no library call before the video stopped)', batch_after_waits == [1, 1] and job.write_failures == 0)
CONDS['Player.HasVideo + !Player.Paused'] = False
importer.monitor = _Monitor()

# --- a title moved to Plan to watch or removed on Simkl must not come back from the kept copy ---
calls = []


def moved_to_plan(path, timeout=30):
    calls.append(path)
    if path == '/sync/activities':
        return {'all': '2026-10-06T00:00:00Z', 'tv_shows': {'completed': '2026-10-01T00:00:00Z', 'plantowatch': '2026-10-06T00:00:00Z' if len(calls) > 1 else ''}}
    if path.startswith('/sync/all-items/shows?'):
        return {'shows': [SHOW_ROW]}
    if path.startswith('/sync/all-items?'):
        return {'shows': [dict(SHOW_ROW, status='plantowatch', seasons=[])]}
    return {}


utils.jsonrpc_batch = library_batch
importer._get = moved_to_plan
library([1])
importer._save({})
importer._run('check', False, False)
calls.clear()
importer._run('check', False, False)
writes_seen.clear()
library([1, 2])
importer._run('check', False, False)
check('cache: a move to Plan to watch on Simkl refreshes the kept copy, so the grown library takes no mark from it',
      any(p.startswith('/sync/all-items?') for p in calls) and store.read(store.HISTORY, None)['shows']['9']['status'] == 'plantowatch'
      and [w for w in writes_seen if w[0] == 'VideoLibrary.SetEpisodeDetails'] == [])
calls.clear()


def removed_on_simkl(path, timeout=30):
    calls.append(path)
    if path == '/sync/activities':
        return {'all': '2026-10-07T00:00:00Z', 'tv_shows': {'completed': '2026-10-01T00:00:00Z', 'plantowatch': '2026-10-06T00:00:00Z', 'removed_from_list': '2026-10-07T00:00:00Z'}}
    if path.startswith('/sync/all-items/shows?extended=simkl_ids_only'):
        return {'shows': []}
    return {}


importer._get = removed_on_simkl
importer._run('check', False, False)
once = [p for p in calls if 'simkl_ids_only' in p]
importer._run('check', False, False)
check('cache: a removal on Simkl cuts the kept copy down to the ids Simkl still lists, once per removal stamp',
      once == ['/sync/all-items/shows?extended=simkl_ids_only'] and store.read(store.HISTORY, None)['shows'] == {}
      and [p for p in calls if 'simkl_ids_only' in p] == once and importer._load().get('removed') == {'shows': '2026-10-07T00:00:00Z'})

# --- a type over the ids-only cap is pruned per status; a status over it too keeps the removal stamp for the next run ---
prune_refusals = {'/sync/all-items/shows?extended=simkl_ids_only'}
removal_stamp = ['2026-10-08T00:00:00Z']


def prune_per_status(path, timeout=30):
    calls.append(path)
    if path == '/sync/activities':
        return {'all': '2026-10-07T00:00:00Z', 'tv_shows': {'completed': '2026-10-01T00:00:00Z', 'plantowatch': '2026-10-06T00:00:00Z', 'removed_from_list': removal_stamp[0]}}
    if path in prune_refusals:
        raise ApiError(400, 'max_items', 'too many')
    if 'simkl_ids_only' in path:
        return {'shows': []}
    return {}


def kept_copy_with_show():
    store.write(store.HISTORY, {'account': 'A', 'shows': {'9': {'show': {'ids': {'simkl': 9, 'tvdb': '9'}, 'title': 'S'}, 'status': 'completed'}}, 'anime': {}, 'movies': {}})


importer.auth = Auth(None)
importer._get = prune_per_status
kept_copy_with_show()
calls.clear()
importer._run('check', False, False)
per_status = [p for p in calls if 'simkl_ids_only' in p]
check('cache: a type over the ids-only cap is listed per status for the prune, the removed title leaves the copy and the stamp is saved',
      per_status[0] == '/sync/all-items/shows?extended=simkl_ids_only' and len(per_status) == 6 and store.read(store.HISTORY, None)['shows'] == {}
      and importer._load().get('removed') == {'shows': '2026-10-08T00:00:00Z'})
removal_stamp[0] = '2026-10-09T00:00:00Z'
prune_refusals.add('/sync/all-items/shows/watching?extended=simkl_ids_only')
kept_copy_with_show()
calls.clear()
importer._run('check', False, False)
kept_stamp = importer._load().get('removed')
still_there = '9' in store.read(store.HISTORY, None)['shows']
prune_refusals.discard('/sync/all-items/shows/watching?extended=simkl_ids_only')
calls.clear()
importer._run('check', False, False)
check('cache: a status over the cap too leaves the copy and keeps the old stamp, so the next run asks again and reconciles',
      kept_stamp == {'shows': '2026-10-08T00:00:00Z'} and still_there and any('simkl_ids_only' in p for p in calls)
      and store.read(store.HISTORY, None)['shows'] == {} and importer._load().get('removed') == {'shows': '2026-10-09T00:00:00Z'})
importer._get = removed_on_simkl

# --- the kept copy is parsed only when something is to be applied to it; a missing file brings a full run; one that cannot be written fails the run ---
reads = []
real_store_read = store.read
store.read = lambda name, default: reads.append(name) or real_store_read(name, default)
importer._run('check', False, False)
quiet = reads.count(store.HISTORY)
library([1, 2, 3])
importer._run('check', False, False)
grew = reads.count(store.HISTORY) - quiet
store.read = real_store_read
os.remove(store.path(store.HISTORY))
calls.clear()
importer._run('check', False, False)
check('cache: not parsed when nothing is to be applied, parsed once when the library grew, and a missing file brings the whole history again',
      quiet == 0 and grew == 1 and [p.split('?')[0] for p in calls] == ['/sync/activities', '/sync/all-items/shows', '/sync/all-items/anime', '/sync/all-items/movies']
      and store.read(store.HISTORY, None) is not None)


def failing_history_write(name, data):
    if name == store.HISTORY:
        raise OSError('disk full')
    return real_store_write(name, data)


try:
    importer._save_cache({'account': 'A'})
    outcome = None
except imp.RunError as exc:
    outcome = str(exc)
os.remove(store.path(store.HISTORY))
store.write = failing_history_write
importer._run('check', False, False)
failed_status = SETTINGS.get('import_status', '')
store.write = real_store_write
calls.clear()
importer._run('check', False, False)
check('cache: a kept copy that cannot be written fails the run with its own message, and the next run fetches everything again',
      outcome is None and failed_status.startswith('str32231') and 'str32313' in failed_status
      and [p.split('?')[0] for p in calls] == ['/sync/activities', '/sync/all-items/shows', '/sync/all-items/anime', '/sync/all-items/movies'] and store.read(store.HISTORY, None) is not None)


def delta_after(path, timeout=30):
    calls.append(path)
    if path == '/sync/activities':
        # no removal moved: the stored removal stamp stays whatever it is, so nothing is pruned behind the delta
        return {'all': '2026-10-10T00:00:00Z', 'tv_shows': {'completed': '2026-10-10T00:00:00Z', 'plantowatch': '2026-10-06T00:00:00Z'}}
    if path.startswith('/sync/all-items?'):
        return {'shows': [dict(SHOW_ROW, status='watching')]}
    return {}


importer._get = delta_after
before_state = importer._load()
store.write = failing_history_write
calls.clear()
importer._run('check', False, False)
failed_delta = (SETTINGS.get('import_status', ''), [p for p in calls if p.startswith('/sync/all-items?')], importer._load())
store.write = real_store_write
calls.clear()
importer._run('check', False, False)
check('cache: a delta whose kept copy cannot be written fails with its message and moves no cursor, stamp or rating stamp; the next run asks from the same point and updates the copy',
      'str32313' in failed_delta[0] and all(failed_delta[2].get(key) == before_state.get(key) for key in ('cursors', 'removed', 'rated', 'synced_at'))
      and len(failed_delta[1]) == 1 and [p for p in calls if p.startswith('/sync/all-items?')] == failed_delta[1]
      and store.read(store.HISTORY, None)['shows']['9']['status'] == 'watching' and importer._load()['cursors']['shows'] == '2026-10-10T00:00:00Z')
importer._get = removed_on_simkl
RPC['VideoLibrary.GetMovies'] = {'movies': [{'movieid': 1, 'title': 'X', 'uniqueid': {'imdb': 'tt1'}, 'playcount': 1, 'userrating': 0}], 'limits': {'start': 0, 'end': 1, 'total': 1}}
RPC['VideoLibrary.GetTVShows'] = {'tvshows': []}
RPC['VideoLibrary.GetEpisodes'] = {'episodes': [], 'limits': {'start': 0, 'end': 1, 'total': 0}}
store.write(store.HISTORY, {'account': 'A', 'shows': {}, 'anime': {}, 'movies': {'7': {'movie': {'ids': {'simkl': 7, 'imdb': 'tt1'}, 'title': 'X'}, 'status': 'completed', 'user_rating': 6}}})
importer._save({'account': 'A', 'full_import_at': '2026-10-01T00:00:00Z', 'synced_at': '2026-10-05T00:00:00Z', 'cursors': {'movies': '2026-10-01T00:00:00Z'},
                'rated': {'movies': '2026-10-01T00:00:00Z'}, 'library_totals': {'movies': 0, 'movies_unwatched': 0, 'episodes': 0, 'episodes_unwatched': 0}})


def rating_moved(path, timeout=30):
    calls.append(path)
    if path == '/sync/activities':
        return {'all': '2026-10-06T00:00:00Z', 'movies': {'completed': '2026-10-01T00:00:00Z', 'rated_at': '2026-10-06T00:00:00Z'}}
    if path.startswith('/sync/ratings?'):
        return {'movies': [{'movie': {'ids': {'simkl': 7, 'imdb': 'tt1'}}, 'user_rating': 9}]}
    return {}


importer._get = rating_moved
writes_seen.clear()
importer._run('check', False, False)
check('cache: Simkl\'s newer rating is applied before the kept copy, so a library growth cannot write the old rating',
      [w[1]['userrating'] for w in writes_seen if w[0] == 'VideoLibrary.SetMovieDetails'] == [9] and store.read(store.HISTORY, None)['movies']['7']['user_rating'] == 9)
for method in ('VideoLibrary.GetMovies', 'VideoLibrary.GetTVShows', 'VideoLibrary.GetEpisodes'):
    RPC.pop(method)
utils.jsonrpc_batch = real_batch
importer._get = lambda path, timeout=30: PAUSE

# --- the scheduled check runs only for a box in use since the last one ---
SETTINGS['auto_import'] = 'true'
SETTINGS['auto_import_hours'] = '1'
importer.auth = PassAuth()
started = []
importer.start = lambda *a, **k: started.append(k) or True
library([1])
importer._save({'library_totals': {'movies': 0, 'movies_unwatched': 0, 'episodes': 1, 'episodes_unwatched': 1}})
now = time.time()
importer.last_auto = now - 7200
importer.used_at = 0.0
importer.looked_at = 0.0
importer.idle_from = now - 36000
IDLE[0] = 36000
importer.auto_check()
unused = list(started)
IDLE[0] = 5
importer.auto_check()
check('gated timer: an unused box skips the check once the interval passed, a key press runs it within the minute',
      unused == [] and started == [{'manual': False, 'scheduled': True}])
started.clear()
importer.idle_from = time.time()
IDLE[0] = 0
importer.auto_check()
fresh = list(started)
importer.idle_from = time.time() - 100
IDLE[0] = 100
importer.auto_check()
booted = list(started)
IDLE[0] = 50
importer.auto_check()
check('gated timer: Kodi merely started is not in use, an input after the start is, even before the first evaluation', fresh == [] and booted == [] and len(started) == 1)
started.clear()
IDLE[0] = 36000
importer.idle_from = time.time() - 36000
importer.used_at = time.time()
importer.auto_check()
played = list(started)
started.clear()
importer.used_at = 0.0
importer.looked_at = 0.0
library([1, 2])
importer.auto_check()
grew = list(started)
started.clear()
library([1, 2, 3])
importer.auto_check()
check('gated timer: a playback since the last check runs it, a changed library too, looked at once per interval',
      len(played) == 1 and len(grew) == 1 and started == [])
del importer.start
importer.auth = None
importer.last_auto = None
importer.used_at = 0.0
importer.looked_at = 0.0
importer.idle_from = time.time()
IDLE[0] = 0
SETTINGS.pop('auto_import')
SETTINGS.pop('auto_import_hours')
for method in ('VideoLibrary.GetMovies', 'VideoLibrary.GetTVShows', 'VideoLibrary.GetEpisodes'):
    RPC.pop(method)

# --- a removed rating leaves the kept copy; a write re-checks Kodi's rating and the queue right before it ---
job = imp.Job(False)
cache = {'movies': {'7': {'movie': {'ids': {'simkl': 7, 'imdb': 'tt1'}}, 'status': 'completed', 'user_rating': 6}}, 'shows': {}, 'anime': {}}
index = index_with_movie()
index.movies['imdb:tt1'][0]['playcount'] = 1
importer._get = lambda path, timeout=30: {'movies': [{'movie': {'ids': {'simkl': 7, 'imdb': 'tt1'}}, 'user_rating': None}]}
importer._changed_ratings(job, index, {'rated': {'movies': '2026-10-01T00:00:00Z'}}, {'movies': '2026-10-02T00:00:00Z', 'shows': '', 'anime': ''}, cache)
importer._apply(job, index, 'movies', list(cache['movies'].values()))
check('ratings: a removal on Simkl comes back through the unfiltered delta and clears the kept copy, nothing is written back',
      'user_rating' not in cache['movies']['7'] and job.rating_writes == [])
job = imp.Job(False)
cache = {'movies': {'7': {'movie': {'ids': {'simkl': 7, 'imdb': 'tt1'}}, 'status': 'completed', 'user_rating': 6},
                    '8': {'movie': {'ids': {'simkl': 8, 'imdb': 'tt8'}}, 'status': 'completed', 'user_rating': 4}}, 'shows': {}, 'anime': {}}
asked_paths = []
importer._get = lambda path, timeout=30: asked_paths.append(path) or {'movies': [{'movie': {'ids': {'simkl': 8, 'imdb': 'tt8'}}, 'user_rating': 4}]}
importer._changed_ratings(job, index, {}, {'movies': '2026-10-02T00:00:00Z', 'shows': '', 'anime': ''}, cache)
importer._apply(job, index, 'movies', list(cache['movies'].values()))
whole = ('user_rating' not in cache['movies']['7'], cache['movies']['8'].get('user_rating'), job.rating_writes == [], asked_paths)
job = imp.Job(False)
cache['movies']['7']['user_rating'] = 6
importer._get = lambda path, timeout=30: (_ for _ in ()).throw(ApiError(400, 'max_items', 'too many'))
importer._changed_ratings(job, index, {}, {'movies': '2026-10-02T00:00:00Z', 'shows': '', 'anime': ''}, cache)
check('ratings: a whole-type fetch (no stamp stored: the import was off) clears the kept rating of every title it does not name, so a later replay cannot write a removed rating back; a capped fetch clears nothing',
      whole == (True, 4, True, ['/sync/ratings/movies/1,2,3,4,5,6,7,8,9,10']) and cache['movies']['7'].get('user_rating') == 6 and 'movies' in job.unrated)
importer._get = lambda path, timeout=30: PAUSE
utils.jsonrpc_batch = rating_batch
outcomes = []
for live, queued in ((9, False), (0, True), (0, False)):
    job = imp.Job(False)
    job.rating_writes = [(dict(ROW), {'userrating': 6})]
    job.rating_ids[('movie', 1)] = {'imdb': 'tt1'}
    ledger.entries = []
    if queued:
        ledger.add_rating(('movies', {'imdb': 'tt1'}, 'X'), 9)
    LIVE_RATING[0] = live
    calls_seen.clear()
    importer._write_ratings(job)
    outcomes.append((len(calls_seen), job.changed))
ledger.entries = []
LIVE_RATING[0] = 0
check('ratings: a rating changed in Kodi since the index was read, or one queued for Simkl meanwhile, is not overwritten', outcomes == [(0, 1), (0, 1), (1, 0)])
job = imp.Job(False)
job.rating_writes = [(dict(ROW, key='movie:%d' % n, dbid=n), {'userrating': 6}) for n in range(1, imp.CHUNK + 2)]
for n in range(1, imp.CHUNK + 2):
    job.rating_ids[('movie', n)] = {'imdb': 'tt%d' % n}
real_batch_stub = utils.jsonrpc_batch


def queue_between_chunks(calls):
    # a rating from the Simkl menu lands while the first chunk is being written: the second chunk must see it
    if calls[0][0].startswith('VideoLibrary.Set') and not ledger.entries:
        ledger.add_rating(('movies', {'imdb': 'tt%d' % (imp.CHUNK + 1)}, 'Late'), 9)
    return rating_batch(calls)


utils.jsonrpc_batch = queue_between_chunks
calls_seen.clear()
importer._write_ratings(job)
check('ratings: a rating queued between two batches is seen by the next batch', len(calls_seen) == imp.CHUNK and job.changed == 1 and ('movieid', imp.CHUNK + 1) not in [(k, v) for c in calls_seen for k, v in c[1].items()])
ledger.entries = []
utils.jsonrpc_batch = real_batch

# --- limits Simkl names: the daily quota holds the token until the reset, a short limit waits a few seconds ---
class JsonError(urllib.error.HTTPError):
    def __init__(self, status, body, headers=None):
        urllib.error.HTTPError.__init__(self, 'https://api.simkl.com/x', status, 'Error', headers or {}, None)
        self.body = body

    def read(self):
        return json.dumps(self.body).encode('utf-8')


sent = []
api = apimod.Api()
urllib.request.urlopen = lambda req, timeout=30: (_ for _ in ()).throw(JsonError(429, {'error': 'user_limit_exceeded'}, {'Retry-After': '3600'}))
try:
    api.call('GET', '/sync/activities', token='simkl_at_A')
    first = None
except ApiError as exc:
    first = exc
urllib.request.urlopen = lambda req, timeout=30: sent.append(req) or FakeResponse(200, b'{"ok": 1}')
try:
    api.call('GET', '/sync/activities', token='simkl_at_A')
    held = None
except ApiError as exc:
    held = exc
held_sent = len(sent)
public = api.call('GET', '/anime/5')
check('quota: a daily-limit 429 carries Retry-After, is quota and retryable but no short limit; that token is held without a request, a call without the token goes out',
      first is not None and first.quota and first.retryable and not first.rate_limited and first.retry_after == 3600
      and held is not None and held.quota and 0 < held.retry_after <= 3601 and held_sent == 0 and public == {'ok': 1} and len(sent) == 1)
api = apimod.Api()
urllib.request.urlopen = lambda req, timeout=30: (_ for _ in ()).throw(JsonError(429, {'error': 'rate_limit'}, {'Retry-After': '50000'}))
try:
    api.call('GET', '/x', token='simkl_at_A')
    short = None
except ApiError as exc:
    short = exc
urllib.request.urlopen = lambda req, timeout=30: FakeResponse(200, b'{"ok": 1}')
check('quota: a 429 rate_limit is a short limit, not a quota, holds nothing, and request still answers two values',
      short is not None and short.rate_limited and not short.quota and api.call('GET', '/x', token='simkl_at_A') == {'ok': 1} and api.request('GET', '/x') == (200, {'ok': 1}))
urllib.request.urlopen = real_urlopen
q = fresh_queue()
q.auth = PassAuth()
q.add_mark(ITEM)
entry = q.entries[0]
toasts = []
utils.notify = lambda message, *a, **k: toasts.append(message)
q.api = types.SimpleNamespace(call=lambda *a, **k: (_ for _ in ()).throw(ApiError(400, 'RATE_LIMIT', 'locked')))
now = time.time()
limit = q._send([entry], '/sync/history', True, 'A')
limit_due = q.due
q.api = types.SimpleNamespace(call=lambda *a, **k: (_ for _ in ()).throw(ApiError(429, 'user_limit_exceeded', 'daily', retry_after=7200)))
quota = q._send([entry], '/sync/history', True, 'A')
quota_due = q.due
q._send([entry], '/sync/history', True, 'A')
eid = q.watch_event(dict(ITEM, key='movie:2', dbid=2), time.time(), False)
q.release(eid, error=ApiError(400, 'RATE_LIMIT', 'locked'))
utils.notify = real_notify
check('limits: a write lock waits 5 s with no back-off and no outage toast, a daily quota waits for the reset with one toast; a refused stop releases its watch the same way',
      limit is False and quota is False and kinds(q) == [h.MARK, h.WATCH] and abs(limit_due - (now + h.RATE_RETRY)) < 2 and abs(quota_due - (now + 7200)) < 2
      and q.backoff == h.BACKOFF_MIN and toasts == ['str32245 {0}'] and not q.entries[1]['inflight'] and abs(q.due - (time.time() + h.RATE_RETRY)) < 2)
waits = []
importer.monitor = types.SimpleNamespace(waitForAbort=lambda seconds: waits.append(seconds) or False, abortRequested=lambda: False)
importer.auth = PassAuth()
importer.api = types.SimpleNamespace(call=lambda *a, **k: (_ for _ in ()).throw(ApiError(429, 'user_limit_exceeded', 'daily', retry_after=21600)))
try:
    imp.Importer._get(importer, '/sync/activities')
    quota_run = None
except imp.RunError as exc:
    quota_run = exc
answers = [ApiError(429, 'rate_limit', ''), ApiError(429, 'rate_limit', ''), {'ok': 1}]


def limited(*a, **k):
    answer = answers.pop(0)
    if isinstance(answer, Exception):
        raise answer
    return answer


importer.api = types.SimpleNamespace(call=limited)
got = imp.Importer._get(importer, '/sync/activities')
check('limits: a quota ends the run at once and holds the next scheduled check until the reset; a short limit is retried after 5 s',
      quota_run is not None and abs(importer.retry_at - (time.time() + 21600)) < 2 and got == {'ok': 1} and waits == [5, 5])
SETTINGS['auto_import'] = 'true'
SETTINGS['auto_import_hours'] = '1'
importer.last_auto = time.time() - 7200
IDLE[0] = 5
started = []
importer.start = lambda *a, **k: started.append(k) or True
importer.auto_check()
importer.retry_at = 0.0
importer.auto_check()
check('limits: the scheduled check waits for the quota reset, then runs', started == [{'manual': False, 'scheduled': True}])
del importer.start
importer.monitor = _Monitor()
importer.api = None
importer.auth = None

# --- the gate's other traps: the run stamp is written first, transport retries are capped, no library gate without a baseline ---
importer.auth = PassAuth()
importer.retries = 0
library([1])
importer._save({})
stamps = []


def transport_fail(path, timeout=30):
    stamps.append(importer._load().get('last_auto_run_at'))
    raise ApiError(0, 'transport', 'down')


importer._get = transport_fail
moved = []
for _ in range(5):
    importer.last_auto = time.time() - 7200
    importer._run('check', False, True)
    moved.append(importer.last_auto < time.time() - 1000)
check('gated timer: the run stamp is on disk before the first request; a transport failure is retried three times per interval, then waits',
      all(stamps) and moved == [True, True, True, False, False] and importer.retries == 3)
importer._get = lambda path, timeout=30: PAUSE
importer.retries = 0
importer.looked_at = 0.0
importer._save({})
check('gated timer: without a baseline the library gate stays shut', importer._library_changed(time.time()) is False)
importer.looked_at = 0.0
importer.last_auto = None
importer.auth = None
IDLE[0] = 0
SETTINGS.pop('auto_import')
SETTINGS.pop('auto_import_hours')
for method in ('VideoLibrary.GetMovies', 'VideoLibrary.GetTVShows', 'VideoLibrary.GetEpisodes'):
    RPC.pop(method)

# --- the ledger belongs to its account: a reconnect, a sign-out and a sign-in to the same account keep it ---
q = fresh_queue()
q.unmarked = {'episode:11': T2, 'episode:12': T2}
q.unmarked_account = 'A'
q.sent_unmarks = {'episode:11': T2}
ident(account_id='')
q.on_login('', 'A')
after_wipe = dict(q.unmarked)
q.on_logout()
q.on_login('', 'A')
q7 = h.History(api=None, auth=None)
reloaded = (dict(q.unmarked), q7.unmarked_account, dict(q7.unmarked), dict(q7.sent_unmarks))
q.on_login('A', 'B')
check('ledger: a reconnect after a rejected token, a sign-out and a sign-in to the same account keep it; another account wipes it',
      after_wipe == {'episode:11': T2, 'episode:12': T2} and reloaded == ({'episode:11': T2, 'episode:12': T2}, 'A', {'episode:11': T2, 'episode:12': T2}, {'episode:11': T2})
      and q.unmarked == {} and q.sent_unmarks == {} and q.unmarked_account == 'B')
ident(token='simkl_at_A', refresh_token='rA', account_id='A')
q = fresh_queue()
q.unmarked = {'movie:5': T2}
q._save()
q.forget_unmarks({'movie:5': T2 - 1})
check('ledger: a queue without an owner gets the connected account at its next save; a settled time older than the entry keeps it', q.unmarked_account == 'A' and q.unmarked == {'movie:5': T2})
job = imp.Job(False)
job.unmarked = {'movie:1': T2, 'movie:2': T2}
job.removed = {'movie:2'}
imp.Importer._mark(job, dict(ROW), None, T2 + 100)
imp.Importer._mark(job, dict(ROW, key='movie:2', dbid=2), None, T2 + 100)
check('ledger: a show-level date re-marks an item whose unmark Simkl applied (a removal, then "season watched" on the site), not one Simkl never got',
      [row['dbid'] for row, _ in job.writes] == [2] and job.kept == 1 and job.rewatched == ['movie:2'])

# --- the question in the service: one at a time, never over a playing video, the answer sends or keeps ---
real_player = service.player
service.player = types.SimpleNamespace(isPlayingVideo=lambda: True)
service.history.entries, service.history.unmarked, service.history.sent_unmarks = [], {}, {}
service.questions.clear()
service.question_open = False
YESNO.clear()
toasts = []
utils.notify = lambda message, *a, **k: toasts.append(message)
service.history.add_unmark(ITEM, explicit=True)
service.history.add_unmark(dict(ITEM, key='movie:2', dbid=2, label='Y'), explicit=True)
service._drain()
service._ask_next()
playing = len(YESNO)
service.player = types.SimpleNamespace(isPlayingVideo=lambda: False)
ANSWER[0] = 2
service._ask_next()
time.sleep(0.2)
one_open = (len(YESNO), service.question_open)
service._drain()
ANSWER[0] = -1
service._ask_next()
time.sleep(0.2)
service._drain()
utils.notify = real_notify
service.player = real_player
first_entry = service.history.entries[0] if service.history.entries else {}
check('question: none over a playing video; then one at a time, Plan to watch recorded without a toast (it comes once Simkl applied it), a closed question keeps the movie on Simkl',
      playing == 0 and one_open == (1, True) and len(YESNO) == 2 and YESNO[0][1] == 'str32305 X (2020)' and YESNO[0][2:6] == ('str32307 {0}', 'str32308 {0}', 'str32306 {0}', service_module.QUESTION_MS)
      and len(service.history.entries) == 1 and first_entry.get('action') == 'plan' and not first_entry.get('asking') and toasts == []
      and 'movie:2' in service.history.unmarked and not service.question_open)
service.history.entries = []

# --- a RunScript unmark of a library movie: Kodi's echo of this add-on's own write must not ask the question again ---
ASKS.clear()
q = fresh_queue()
q.add_unmark(ITEM, explicit=True)
explicit_id = q.entries[0]['id']
q.add_unmark(ITEM, arrived=time.time())
q._settle_window(time.time() + 5)
check('echo: the library echo of an explicit movie unmark queues nothing: one question, the explicit entry stays',
      len(ASKS) == 1 and [e['id'] for e in q.entries] == [explicit_id] and q.entries[0].get('explicit') and q.window_unmarks == [])
ident(token='simkl_at_A', account_id='A')
api = RemoveApi()
q.api, q.auth = api, PassAuth()
q.answer_unmark(explicit_id, 'remove')
sender = FakeSender()
q.tick(time.time() + 5, sender)
for job in sender.jobs:
    job()
check('echo: the answer to that one question is sent once', api.paths == [WATCHED, REMOVE] and len(api.removed) == 1 and q.entries == [])
ASKS.clear()
q = fresh_queue()
q.add_unmark(ITEM, arrived=time.time())
q._settle_window(time.time() + 5)
check('echo: a library unmark with no explicit entry still asks once', len(ASKS) == 1 and len(q.entries) == 1 and q.entries[0].get('asking'))
ASKS.clear()
q = fresh_queue()
q.add_unmark(ITEM, explicit=True)
q.add_unmark(ITEM, arrived=time.time())
q.answer_unmark(q.entries[0]['id'], 'keep')
q._settle_window(time.time() + 5)
check('echo: a question answered "keep" within the window leaves nothing queued and is not asked again', len(ASKS) == 1 and q.entries == [] and ITEM['key'] in q.unmarked)


class RemoveFailsApi(RemoveApi):
    """The list check answers, the removal itself fails."""
    def call(self, method, path, body=None, token=None, timeout=30):
        if path.startswith('/sync/history/remove'):
            raise ApiError(500, 'server_error', 'down')
        return RemoveApi.call(self, method, path, body, token, timeout)


asked = []
toasts = []
utils.notify = lambda message, *a, **k: toasts.append(message)
q = h.History(api=RemoveFailsApi(), auth=PassAuth(), ask=asked.append)
q.entries, q.unmarked, q.window_unmarks = [], {}, []
q.add_unmark(ITEM, explicit=True)
q.answer_unmark(asked[0], 'remove')
sender = FakeSender()
q.tick(time.time() + 5, sender)
for job in sender.jobs:
    job()
utils.notify = real_notify
check('question: a removal whose request fails shows no "removed" toast (only the retry one) and stays queued',
      not any(t.startswith(('str32256', 'str32254')) for t in toasts) and kinds(q) == [h.UNMARK] and ITEM['key'] not in q.unmarked)

# --- unsent writes keep for 30 days ---
q = fresh_queue()
q.add_mark(ITEM)
q.entries[0]['time'] = time.time() - 20 * 86400
check('queue: an unsent mark survives 20 days (30-day cap)', h.MAX_AGE == 30 * 86400 and len(q._eligible()) == 1)
toasts = []
utils.notify = lambda message, *a, **k: toasts.append(message)
q = fresh_queue()
for n in (1, 2, 3):
    q.add_mark(dict(ITEM, key='movie:%d' % n, dbid=n))
    q.entries[-1]['time'] = time.time() - 31 * 86400
q.add_mark(dict(ITEM, key='movie:4', dbid=4))
eligible = q._eligible()
again = q._eligible()
utils.notify = real_notify
check('queue: three writes past 30 days are dropped with one toast naming their count, the next look says nothing', [e['key'] for e in eligible] == ['movie:4'] and again == eligible and toasts == ['str32311 3'])
ident(token='simkl_at_A', account_id='A')


class OfflineThenOnline(HistoryApi):
    """Simkl unreachable (a transport failure) until told otherwise."""
    def __init__(self):
        HistoryApi.__init__(self)
        self.offline = True

    def call(self, method, path, body=None, token=None, timeout=30):
        if self.offline:
            raise ApiError(0, 'transport', 'down')
        return HistoryApi.call(self, method, path, body, token, timeout)


api = OfflineThenOnline()
q = h.History(api=api, auth=PassAuth())
q.entries, q.unmarked, q.window_unmarks = [], {}, []
old = time.time() - 5 * 86400
q.release(q.watch_event(dict(ITEM), old, False), toast=False)
sender = FakeSender()
q.due = 0
q.tick(time.time() + 5, sender)
for job in sender.jobs:
    job()
held = api.sent == [] and kinds(q) == [h.WATCH] and q.due > time.time()
api.offline = False
# Kodi's own "internet state" is not consulted any more: its probe tests other hosts than Simkl, under the GUI lock
CONDS['System.InternetState'] = False
sender = FakeSender()
q.due = 0
q.tick(time.time() + 5, sender)
for job in sender.jobs:
    job()
CONDS.pop('System.InternetState')
check('queue: a watch kept for five days while Simkl was unreachable goes out with its own date once it answers, whatever Kodi says about the internet',
      held and [b['movies'][0]['watched_at'] for _, b in api.sent] == [utils.utc_stamp(old)] and q.entries == [])

# --- a rewatch needs real viewing: 10% of the runtime really watched, not a minute of the ending ---
ident(token='simkl_at_A', account_id='A', account_plan='pro')
SETTINGS['allow_rewatch'] = 'true'
stop_paths = []


def viewing(start, seconds, speed=1, paused=False):
    """A 3600 s item: a seek to `start`, then `seconds` seconds sampled at `speed` (or paused), stopped at 100%.
    Returns (the stop's path, the rewatch flags of the queued watch and its sibling, the seconds counted as watched)."""
    player = FakePlayer(position=0.0, total=3600.0)
    scrobbler = scr.Scrobbler(api=None, auth=passing, player=player, sender=FakeSender(), history=fresh_queue(), marks=FakeMarks())
    scrobbler._deliver = lambda session, path, progress: stop_paths.append(path) or {'action': 'scrobble'}
    session = scr.Session(1)
    session.item = dict(ITEM)
    session.siblings = [dict(EPISODE)]
    session.started = True
    session.total = 3600.0
    session.speed, session.paused = speed, paused
    scrobbler.session = session
    now = time.time()
    session.sampled_at = now
    player.position = float(start)
    scrobbler.tick(now + 1)
    for second in range(1, seconds + 1):
        player.position = float(start) + (0 if paused else second * max(speed, 1))
        scrobbler.tick(now + 1 + second)
    scrobbler._stop(session, 100.0)
    # both write-ahead entries before the stop's live answer acks the main one (the sibling waits for the flush)
    flags = [e['rewatch'] for e in scrobbler.history.entries]
    scrobbler.sender.jobs[-1]()
    return stop_paths[-1], flags, session.played


ending = viewing(3300, 60)
full = viewing(0, 600)
paused_out = viewing(0, 600, paused=True)
forwarded = viewing(0, 600, speed=2)
tempo = viewing(0, 300, speed=1.5)
check('rewatch: a minute of the ending is no rewatch (the stop goes without allow_rewatch), ten minutes of a 60-minute item are, five are not',
      ending[0] == '/scrobble/stop?min_progress=70' and ending[1] == [False, False] and 55 <= ending[2] <= 60
      and full[0] == '/scrobble/stop?min_progress=70&allow_rewatch=yes' and full[1] == [True, True] and full[2] >= 540
      and viewing(0, 300)[1] == [False, False] and viewing(0, 361)[1] == [True, True] and scr.REWATCH_SHARE == 0.10)
check('rewatch: the seek itself, paused time and fast-forwarding count nothing; a faster tempo still counts as viewing',
      paused_out[2] == 0 and not paused_out[1][0] and forwarded[2] == 0 and not forwarded[1][0] and 295 <= tempo[2] <= 300 and not tempo[1][0]
      and viewing(0, 600, speed=1.5)[1] == [True, True])
SETTINGS.pop('allow_rewatch')
ident(token='simkl_at_A', account_id='A', account_plan='')


# --- an entry's own wait: cleared once its list check answers, capped at an hour, the rest of the flush unaffected ---
class ListThenFailApi(RemoveApi):
    """The watched check fails `fails` times, then answers; the removal itself fails by transport."""
    def __init__(self, fails):
        RemoveApi.__init__(self)
        self.fails = fails

    def call(self, method, path, body=None, token=None, timeout=30):
        if path.startswith('/sync/watched') and self.fails > 0:
            self.fails -= 1
            raise ApiError(500, 'server_error', 'down')
        if path.startswith('/sync/history/remove'):
            raise ApiError(0, 'transport')
        return RemoveApi.call(self, method, path, body, token, timeout)


def flush_now(queue):
    for entry in queue.entries:
        entry['retry_at'] = 0
    queue.due = 0
    sender = FakeSender()
    queue.tick(time.time() + 5, sender)
    for job in sender.jobs:
        job()


ident(token='simkl_at_A', account_id='A')
asked = []
q = h.History(api=ListThenFailApi(2), auth=PassAuth(), ask=asked.append)
q.entries, q.unmarked, q.window_unmarks = [], {}, []
q.add_unmark(ITEM, explicit=True)
q.answer_unmark(asked[0], 'remove')
waits = []
for _ in range(3):
    flush_now(q)
    waits.append((q.entries[0].get('retry_wait'), 'retry_at' in q.entries[0]))
on_disk = store.read(store.QUEUE, {}).get('entries', [{}])[0]
q.api.fails = 1
flush_now(q)
check('retry: a movie unmark whose check failed twice (60 s) loses its wait once the check answers, on disk too, and a later failure starts again at 30 s',
      waits == [(30, True), (60, True), (None, False)] and 'retry_wait' not in on_disk and 'retry_at' not in on_disk and kinds(q) == [h.UNMARK] and q.entries[0]['retry_wait'] == 30)
entry = {}
check('retry: the wait doubles from 30 s to the hour and stays there', [h._held_back(entry) for _ in range(9)] == [30, 60, 120, 240, 480, 960, 1920, 3600, 3600])
api = HistoryApi()
q = h.History(api=api, auth=PassAuth())
q.entries, q.unmarked, q.window_unmarks = [], {}, []
unnamed = {'kind': None, 'key': r'file:D:\u.mkv', 'dbid': None, 'title': 'u', 'year': None, 'ids': {}, 'file': r'D:\u.mkv', 'runtime': 0, 'label': 'u.mkv'}
for item in (dict(ITEM), dict(ITEM, key='movie:2', dbid=2, ids={'imdb': 'tt2'}), unnamed):
    q.release(q.watch_event(item, time.time() - 10, False), toast=False)
playerlib.search_file = lambda api, auth, account, item: (_ for _ in ()).throw(ApiError(403, 'forbidden'))
flush_now(q)
playerlib.search_file = real_search
check('retry: a search refused for one entry holds that entry back alone, the named watches of the flush go out',
      len(api.sent) == 1 and len(api.sent[0][1]['movies']) == 2 and kinds(q) == [h.WATCH] and not q.entries[0]['item'].get('ids') and q.entries[0]['retry_at'] > time.time())


class TransportAuth:
    """What Auth.authorized raises when the renewal after a 401 fails by transport."""
    def logged_in(self):
        return True

    def authorized(self, call, account=None):
        raise ApiError(0, 'transport')


asked = []
q = h.History(api=RemoveApi(), auth=TransportAuth(), ask=asked.append)
q.entries, q.unmarked, q.window_unmarks = [], {}, []
q.release(q.watch_event(dict(unnamed), time.time() - 10, False), toast=False)
q.add_unmark(ITEM, explicit=True)
q.answer_unmark(asked[0], 'remove')
flush_now(q)
check('retry: a renewal failing by transport keeps an unnamed watch and a movie unmark queued, the queue waiting as a whole before the movie is even asked about',
      sorted(kinds(q)) == [h.UNMARK, h.WATCH] and q.due > time.time() and not any(e.get('retry_at') or e.get('retry_wait') for e in q.entries) and q.api.paths == [])

# --- translations: every language file carries the ids of en_gb with the same placeholders, Unix endings, no BOM ---
import re  # noqa: E402

ADDON_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LANGUAGE_DIR = os.path.join(ADDON_DIR, 'resources', 'language')


def po_entries(path):
    with open(path, 'rb') as handle:
        raw = handle.read()
    text = raw.decode('utf-8')
    entries = {int(m.group(1)): m.group(2) for m in re.finditer(r'msgctxt "#(\d+)"\nmsgid "(?:[^"\\]|\\.)*"\nmsgstr "((?:[^"\\]|\\.)*)"', text)}
    return entries, b'\r\n' in raw, raw.startswith(b'\xef\xbb\xbf')


english_po = os.path.join(LANGUAGE_DIR, 'resource.language.en_gb', 'strings.po')
with open(english_po, 'r', encoding='utf-8') as handle:
    english_ids = {int(m.group(1)): m.group(2) for m in re.finditer(r'msgctxt "#(\d+)"\nmsgid "((?:[^"\\]|\\.)*)"', handle.read())}
translation_problems = []
translation_count = 0
for folder in sorted(os.listdir(LANGUAGE_DIR)):
    if folder == 'resource.language.en_gb' or not folder.startswith('resource.language.'):
        continue
    lang = folder[len('resource.language.'):]
    if lang != lang.lower():
        translation_problems.append('%s: folder not lower-case' % lang)
    entries, crlf, bom = po_entries(os.path.join(LANGUAGE_DIR, folder, 'strings.po'))
    translation_count += 1
    if crlf or bom:
        translation_problems.append('%s: %s' % (lang, 'CRLF' if crlf else 'BOM'))
    for sid, translated in entries.items():
        if sid not in english_ids:
            translation_problems.append('%s: #%d is not in en_gb' % (lang, sid))
        elif translated and sorted(re.findall(r'\{\d\}', translated)) != sorted(re.findall(r'\{\d\}', english_ids[sid])):
            translation_problems.append('%s: #%d placeholders differ' % (lang, sid))
    missing = len(english_ids) - len(set(entries) & set(english_ids))
    if missing > len(english_ids) // 2:
        translation_problems.append('%s: %d of %d en_gb ids missing' % (lang, missing, len(english_ids)))
check('translations: %d language files carry en_gb ids with the same placeholders, lower-case folders, LF, no BOM%s' % (translation_count, (' (' + '; '.join(translation_problems[:5]) + ')') if translation_problems else ''),
      translation_count >= 1 and not translation_problems)

print('\n%d failure(s)' % len(failures))
sys.exit(1 if failures else 0)
