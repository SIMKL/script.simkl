# Simkl TV Tracker for Kodi: developer guide

How an add-on, a skin or a keymap works with Simkl TV Tracker (`script.simkl`, Kodi 19 and newer). The user guide is the [README](../README.md).

## What the add-on needs from you

Simkl TV Tracker reports to Simkl only what it can identify. Items of the Kodi library and local files are identified on their own; an item your add-on plays is identified by the ids you put on it. It never guesses from a title, so an item without ids is simply not tracked (the Kodi log says `not tracked: no ids` with the add-on's debug logging on).

The ids Simkl resolves:

| Key | Value | For |
|---|---|---|
| `imdb` | `tt1375666` | movies, shows |
| `tmdb` | `27205` | movies, shows |
| `tvdb` | `81189` | shows (and an episode's own id) |
| `anidb` | `69` | anime |
| `mal` | `1535` | anime |
| `anilist` | `1535` | anime |
| `simkl` | `2143009` | anything |

Key names are case-insensitive and may carry an `_id` suffix (`tmdb`, `TMDB` and `tmdb_id` are the same). Values may be numbers or strings; empty values and `0` are ignored. One id is enough; several are better, the first one Simkl knows wins.

**Movie or episode** is decided by the item, not by the ids. An item whose media type is `episode`, or that carries a season and an episode number, is an episode, and its ids are the show's. Any other item is a movie, and its ids are the movie's. The same `tmdb` or `tvdb` key therefore means a TV id on an episode item and a movie id on a movie item, since TMDB and TVDB number movies and shows separately. Never put a show's ids on an item without season and episode numbers: Simkl would look them up as movie ids and could land on an unrelated movie.

## Movies

Put the movie's ids on the `ListItem` you hand to the player or return from `setResolvedUrl`. Either of these works:

```python
import json
import xbmcgui

item = xbmcgui.ListItem('Inception')

# 1. Kodi's own unique ids (the way Kodi's library scrapers do it)
item.setUniqueIDs({'tmdb': '27205', 'imdb': 'tt1375666'}, 'tmdb')

# 2. Or one JSON property with the same keys
item.setProperty('script.simkl.ids', json.dumps({'tmdb': 27205, 'imdb': 'tt1375666'}))

# 3. Or flat properties, one per id
item.setProperty('tmdb_id', '27205')
item.setProperty('imdb_id', 'tt1375666')
```

The add-on reads them in that order and takes the first one that carries a usable id. On Kodi 20 and newer `setUniqueIDs` also exists on `item.getVideoInfoTag()`.

## Episodes

An episode needs the **show's** ids plus its season and episode numbers. By Kodi's convention the unique ids of an episode item are the episode's own ids, so the show's ids go into a property:

```python
import json
import xbmcgui

item = xbmcgui.ListItem("Cat's in the Bag...")
item.setInfo('video', {
    'mediatype': 'episode',
    'tvshowtitle': 'Breaking Bad',
    'season': 1,
    'episode': 2,
    'title': "Cat's in the Bag...",
})

# the show's ids, one JSON property
item.setProperty('script.simkl.ids', json.dumps({'tvdb': 81189, 'tmdb': 1396, 'imdb': 'tt0903747'}))

# or flat properties with the tvshow. prefix
item.setProperty('tvshow.tvdb_id', '81189')
item.setProperty('tvshow.tmdb_id', '1396')

# optional: the episode's own TVDB id, sent as the episode's id
item.setUniqueIDs({'tvdb': '349232'}, 'tvdb')
```

Season and episode numbers come from the item's video info (`season`, `episode`); when the item has none, the add-on reads Kodi's `VideoPlayer.Season` and `VideoPlayer.Episode` labels. A season of -1 (unknown) is taken as season 1, never as the specials. An episode without show ids is not tracked, and the Simkl context entry on such a row says so.

Anime is a show on Simkl: give it the show's `anidb`, `mal`, `anilist`, `tvdb` or `simkl` id and the season and episode numbers your listing uses.

## The home-window property

When you cannot touch the `ListItem` (an old playback path, a player you do not build the item for), set the same JSON on the home window before starting the playback:

```python
import json
import xbmcgui

xbmcgui.Window(10000).setProperty('script.simkl.ids', json.dumps({'tmdb': 27205}))
xbmc.Player().play(url)
```

For an episode the window property carries the show's ids; season and episode numbers still come from the playing item or the `VideoPlayer.*` labels.

Rules of the window property:

- Set it before every playback. The add-on clears it when the playback ends, but only the values that were there when the playback began: a value you set for the next item meanwhile stays.
- It is read only for streams and plugin items, never for a local file or a library item, since Kodi announces no end for a playback it replaces in place and a stale value would name the wrong file.
- Prefer the `ListItem` property: it travels with the item through playlists and your own autoplay, and it is the only thing the context menu reads.

## Keeping the add-on out

- Your add-on scrobbles to Simkl itself: set `script.simkl.paused` to `true` (a property of the `ListItem`, or of the home window) and this add-on leaves that playback alone, so the user is not reported twice.

  ```python
  item.setProperty('script.simkl.paused', 'true')
  ```

- One item that must never be reported (a trailer, a preview, a recap): set `script.simkl.exclude` on the `ListItem`, any value.

  ```python
  item.setProperty('script.simkl.exclude', 'true')
  ```

- Users can also switch off tracking of add-on playbacks, HTTP streams and Live TV, and exclude folders, in the add-on's settings. Short add-on streams under the user's minimum length (5 minutes by default) are ignored, and so is a playback much shorter than the runtime of the movie it is reported under.

## What the add-on tells you

### The playing item

While a playback is tracked, the home-window property `script.simkl.playing` holds a JSON object. Cleared when the playback ends.

```json
{"kind": "episode", "label": "Breaking Bad S01E02", "ids": {"tvdb": "81189"}, "dbid": null,
 "showtitle": "Breaking Bad", "season": 1, "number": 2}
```

For a movie the keys are `kind`, `label`, `ids`, `dbid`, `title` and `year`. `dbid` is the Kodi library id when the item is a library item, else `null`.

### Watches recorded on Simkl

When a watch is recorded on Simkl, the add-on sends `NotifyAll(script.simkl, scrobbled, {"data": "<base64 JSON>"})` with the same object. Base64 keeps Kodi's builtin parser from splitting the JSON at its commas. Decode it in your `onNotification`:

```python
import base64
import json
import xbmc

class Monitor(xbmc.Monitor):
    def onNotification(self, sender, method, data):
        if sender == 'script.simkl' and method == 'Other.scrobbled':
            watched = json.loads(base64.b64decode(json.loads(data)['data']).decode('utf-8'))
            # refresh your watched markers for watched['ids'] ...
```

Sent for live scrobbles and for queued watches delivered later, so a watch made offline arrives when Simkl is reachable again.

### Actions for skins and keymaps

`RunScript(script.simkl, action=<name>)` acts on the focused list item, which can be a library item, a movie with ids, or a show or episode row of your own listing that carries the ids described above:

| Action | What it does |
|---|---|
| `action=mark` | Mark as watched on Simkl |
| `action=unmark` | Remove from the Simkl history |
| `action=plantowatch` | Add to Plan to watch |
| `action=rate` | Rate the movie, or the show of the row: `rating=1` to `rating=10`; `rating=0` removes the rating; no rating asks with a pick |
| `action=import_check` | Sync updates from Simkl into the Kodi library |
| `action=show_log` | Show the Simkl lines of the Kodi log |

Without an action the add-on settings open. The same actions sit under the "Simkl" entry of the context menu on movies, shows, seasons, episodes and plain video files; on your own episode rows the entry works when the `ListItem` carries the show's ids, since the menu reads the item alone, never the home window.

## Local files and the file search

A library item without ids and any local file or file on a network share is identified through Simkl's file search by its full path (a share's credentials are stripped first). The folder names count, so a show or a movie in a folder of its own is found more reliably than a bare file name. A file the search places in a series but at no episode is left alone. The answers are kept in the add-on's profile (`search.json`): a hit for 7 days, "no such file" for 3, so a replayed file costs no request.

## Up Next

The `NEXTUPWATCHEDSIGNAL` notification of the Up Next add-on is honoured: the episode Up Next moved on from counts as watched to its end. It is taken only for a playback past its middle, since some Up Next versions send it when their popup closes, also when the next episode has already started; that one is never closed by it.

## Checking your integration

1. Switch on **Debug logging** in the add-on's Advanced settings.
2. Play an item from your add-on. The Kodi log (or **Show the Simkl log**) shows how the add-on identified it, for example `file search identified ... as ...`, or `not tracked: <reason>` when it did not.
3. Read `script.simkl.playing` from the home window during the playback to see exactly what is being tracked.
4. On Simkl, the item shows under "watching now" on your profile while it plays.

Common reasons for `not tracked`:

| Reason | Fix |
|---|---|
| `no ids (type unknown)` | No usable id on the item: set `script.simkl.ids` or unique ids |
| `episode without show ids` | The item has season and episode numbers but no show ids |
| `episode without a number` | Season found, episode number missing or 0 |
| `excluded by the add-on playing it` | `script.simkl.exclude` is set on the item |
| `the add-on playing it scrobbles to Simkl itself` | `script.simkl.paused` is set |
| `trailer` | Kodi's "Play trailer" item, or the library movie's trailer URL |
| a path reason | The user excluded add-on playbacks, HTTP streams, Live TV or that folder in the settings |
