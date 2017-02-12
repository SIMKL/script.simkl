#!/usr/bin/python
# -*- coding: UTF-8 -*-

import xbmc
import interface
import json
__addon__ = interface.__addon__
def getstr(strid): return interface.getstr(strid)

class Engine:
  def __init__(self, api, player):
    self.api = api
    self.player = player
    player.engine = self
    player.api    = api

  def synclibrary(self, mode="quick"):
    """
      Fetches the library from Simkl to Kodi
      Mode can be "quick" or "full"
    """
    xbmc.log("Simkl: Syncing library (Simkl to Kodi)")

    mode = "full"

    if mode == "full":
      todump = {
        "jsonrpc": "2.0",
        "method": "VideoLibrary.GetMovies",
        "params": {
          "limits": {
            "start": 0,
            "end": 1000
          },
          "properties": [
            "playcount",
            "imdbnumber",
            "file",
            "lastplayed",
            "year"
          ],
          "sort": {
            "order": "ascending",
            "method": "playcount",
            #"ignorearticle": True
          }
        },
        "id": "libMovies"
      }
      kodilibrary = json.loads(xbmc.executeJSONRPC(json.dumps(todump)))
      if kodilibrary["result"]["limits"]["total"] > 0:
        xbmc.log(str(kodilibrary))
        progress = interface.SyncProgress("movies", "full")
        each = float(100) / kodilibrary["result"]["limits"]["total"]
        for movie in kodilibrary["result"]["movies"]:
          progress.push(each, "{0} ({1})".format(movie["label"], movie["year"]))
          if movie["playcount"] == 0:
            ### DOWNLOAD FROM KODI
            #Separate big list in chunks
            movie["media"] = "movie"
            if self.api.check_if_watched(movie):
              xbmc.log("Simkl: {0}".format(movie))
              ret = xbmc.executeJSONRPC(json.dumps({
                  "jsonrpc": "2.0",
                  "method": "VideoLibrary.SetMovieDetails",
                  "params": {
                    "playcount": 1,
                    #"lastplayed":"",
                    "movieid":movie["movieid"]
                  }
                }))
              #xbmc.log(ret)
        del progress

      todump["method"] = "VideoLibrary.GetTVShows"
      todump["params"]["properties"] = ["imdbnumber", "title", "watchedepisodes"]
      #If watchedepisodes > 0
      kodilibrary = xbmc.executeJSONRPC(json.dumps(todump))
      kodilibrary = json.loads(kodilibrary)

      if kodilibrary["result"]["limits"]["total"] > 0:
        progress = interface.SyncProgress("TV Shows", "full")
        each = float(100) / kodilibrary["result"]["limits"]["total"]
        debug_cnt = 0
        for tvshow in kodilibrary["result"]["tvshows"]:
          #if debug_cnt >= 10: break #I have a lot of TV Shows, only for testing
          progress.push(each, tvshow["label"])
          debug_cnt += 1


          todump["method"] = "VideoLibrary.GetSeasons"
          #todump["params"]["Library.Id"] = tvshow["tvshowid"]
          todump["id"] = tvshow["tvshowid"]
          todump["params"]["properties"] = ["season", "episode", "watchedepisodes", "showtitle"]
          seasons = xbmc.executeJSONRPC(json.dumps(todump))
          xbmc.log(json.dumps(tvshow))
          xbmc.log(seasons)
          seasons = json.loads(seasons)


          for season in seasons["result"]["seasons"]:
            values = []

            todump["method"] = "VideoLibrary.GetEpisodes"
            todump["params"]["tvshowid"] = tvshow["tvshowid"]
            todump["params"]["season"] = season["season"]
            todump["params"]["properties"] = ["title", "rating", "playcount",
              "season", "episode", "showtitle", "lastplayed", "tvshowid"]

            episodes = xbmc.executeJSONRPC(json.dumps(todump))
            xbmc.log(episodes)
            episodes = json.loads(episodes)

            if episodes["result"]["limits"]["total"] > 0:
              for episode in episodes["result"]["episodes"]:
                values.append({
                    "type": "tv",
                    "season": episode["season"],
                    "episode": episode["episode"],
                    "title": episode["showtitle"],
                    "tvdb": tvshow["imdbnumber"]
                })

              watched = self.api.check_if_watched(values, False)
              xbmc.log(json.dumps(watched))

              for i, episode in enumerate(episodes["result"]["episodes"]):
                toupdate = {
                  "jsonrpc": "2.0",
                  "method": "VideoLibrary.SetEpisodeDetails",
                  "params": {
                    "episodeid":episode["episodeid"],
                    "playcount": int(watched[i]["result"]),
                  },
                  "id": "libMovies"
                }
                try:
                  toupdate["params"]["lastplayed"] = watched[i]["last_watched"]
                except KeyError:
                  toupdate["params"]["lastplayed"] = ""

                info = xbmc.executeJSONRPC(json.dumps(toupdate))
                xbmc.log("Simkl: Info: {0}".format(info))

            del todump["params"]["tvshowid"]
            del todump["params"]["season"]
        del progress

    xbmc.log("Simkl: Finished syncing library")
    interface.notify("Finished syncing library")


class Player(xbmc.Player):
  """ Replaces the Kodi player class """
  def __init__(self):
    xbmc.Player.__init__(self)

  @staticmethod
  def getMediaType():
    """ Returns the MediaType of the file currently playing """
    if xbmc.getCondVisibility('Container.Content(tvshows)'):
      return "show"
    elif xbmc.getCondVisibility('Container.Content(seasons)'):
      return "season"
    elif xbmc.getCondVisibility('Container.Content(episodes)'):
      return "episode"
    elif xbmc.getCondVisibility('Container.Content(movies)'):
      return "movie"
    else:
      return None

  def onPlayBackStarted(self):
    """ Activated at start """
    #self.onPlayBackStopped()
    pass
  def onPlayBackSeek(self, *args):
    """ Activated on seek """
    self.onPlayBackStopped()
  def onPlayBackResumed(self):
    """ Activated on resume """
    self.onPlayBackStopped()
  def onPlayBackEnded(self):
    """ Activated at end """
    xbmc.log("Simkl: ONPLAYBACKENDED")
    self.onPlayBackStopped()

  def onPlayBackStopped(self):
    """ Gets the info needed to pass to the api """
    try:
      movie = self.getVideoInfoTag()
      thing = xbmc.executeJSONRPC(json.dumps({
        "jsonrpc": "2.0",
        "method": "Player.GetItem",
        "params": {
          "properties": [ "showtitle", "title", "season", "episode", "file", "imdbnumber", "genre" ],
          "playerid": 1 },
        "id": "VideoGetItem"
      }))
      item = json.loads(thing)["result"]["item"]
      fname = item["imdbnumber"]
      if fname == "": fname = item["file"]
      media = item["type"]

      percentage = 100 * self.getTime() / self.getTotalTime()
      pctconfig  = int(self.addon.getSetting("scr-pct"))

      if 99 > percentage > pctconfig:
        bubble = __addon__.getSetting("bubble")
        r = self.api.watched(fname, media, self.getTotalTime())

        if bubble=="true" and r:
          title = ""
          lstw = self.api.lastwatched
          if lstw["type"] == "episode":
            txt = lstw["show"]["title"]
            title = "- S{:02}E{:02}".format(lstw["episode"]["season"], lstw["episode"]["episode"])
          elif lstw["type"] == "movie":
            item["title"] = "".join([lstw["movie"]["title"], " (", str(lstw["movie"]["year"]), ")"])
            txt = item["title"]

          interface.notify(getstr(32028).format(title), title=txt)
          r = 0

    except RuntimeError:
      pass
    except ZeroDivisionError:
      self.onPlayBackStopped()
