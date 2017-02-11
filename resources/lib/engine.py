#!/usr/bin/python
# -*- coding: UTF-8 -*-

import os
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

  def synclibrary(self):
    xbmc.log("Simkl: Syncing library (Simkl to Kodi)")
    kodilibrary = xbmc.executeJSONRPC(json.dumps({
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
        "lastplayed"
      ],
      "sort": {
        "order": "ascending",
        "method": "label",
        "ignorearticle": True
      }
      },
      "id": "libMovies"
    }))
    kodilibrary = json.loads(kodilibrary)
    if kodilibrary["result"]["limits"]["total"] > 0:
      for movie in kodilibrary["result"]["movies"]:
        ### DOWNLOAD FROM KODI
        #Separate big list in chunks
        movie["media"] = "movie"
        if self.api.check_if_watched(movie):
          xbmc.log("Simkl: {0}".format(movie))
          ret = xbmc.executeJSONRPC(json.dumps({
              "jsonrpc": "2.0",
              "method": "VideoLibrary.SetMovieDetails",
              "params": {
                "playcount":max(movie["playcount"], 1),
                #"lastplayed":"",
                "movieid":movie["movieid"]
              }
            }))
          xbmc.log(ret)

    todump["method"] = "VideoLibrary.GetTVShows"
    todump["params"]["properties"] = ["imdbnumber", "title", "watchedepisodes"]
    #If watchedepisodes > 0
    kodilibrary = xbmc.executeJSONRPC(json.dumps(todump))
    kodilibrary = json.loads(kodilibrary)
    debug_cnt = 0
    if kodilibrary["result"]["limits"]["total"] >0:
      for tvshow in kodilibrary["result"]["tvshows"]:
        #if debug_cnt >= 3: break #I have a lot of TV Shows, only for testing
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

    #series = self.api.get_all_items("shows")
    #xbmc.log(series)

    xbmc.log("Simkl: Finished syncing library")
    interface.notify("Finished syncing library")


class Player(xbmc.Player):
  def __init__(self):
    xbmc.Player.__init__(self)

  @staticmethod
  def getMediaType():
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
    #self.onPlayBackStopped()
    pass
  def onPlayBackSeek(self, *args):
    self.onPlayBackStopped()
  def onPlayBackResumed(self):
    self.onPlayBackStopped()
  def onPlayBackEnded(self):
    xbmc.log("Simkl: ONPLAYBACKENDED")
    self.onPlayBackStopped()

  def onPlayBackStopped(self):
    ''' Gets the info needed to pass to the api '''
    self.api.check_connection()
    try:
      item = json.loads(xbmc.executeJSONRPC(json.dumps({
        "jsonrpc": "2.0", "method": "Player.GetItem",
        "params": {
          "properties": ["showtitle", "title", "season", "episode", "file", "tvshowid", "imdbnumber", "genre" ],
          "playerid": 1},
        "id": "VideoGetItem"})))["result"]["item"]
      if item["tvshowid"] != -1:
        item["imdbnumber"] = json.loads(xbmc.executeJSONRPC(json.dumps({
          "jsonrpc": "2.0", "method":"VideoLibrary.GetTVShowDetails",
          "params":{"tvshowid":item["tvshowid"], "properties":["imdbnumber"]},
          "id":1
          })))["result"]["tvshowdetails"]["imdbnumber"]
      xbmc.log("Simkl: Full: {0}".format(item))

      percentage = min(99, 100 * self.getTime() / self.getTotalTime())
      pctconfig  = int(self.addon.getSetting("scr-pct"))

      if percentage > pctconfig:
        bubble = __addon__.getSetting("bubble")
        xbmc.log("Simkl: Bubble == {0}".format(bubble))
        xbmc.log("Percentage: {0}, pctconfig {1}".format(percentage, pctconfig))

        r = self.api.watched(item, self.getTotalTime())

        if bubble=="true" and r:
          if item["label"] == os.path.basename(item["file"]):
          #if True: #For testing purposes
            xbmc.log("Simkl: Label and file are the same")
            lstw = self.api.lastwatched
            if lstw["type"] == "episode":
              item["showtitle"] = lstw["show"]["title"]
              item["season"] = lstw["episode"]["season"]
              item["episode"] = lstw["episode"]["episode"]
            elif lstw["type"] == "movie":
              item["title"] = "".join([lstw["movie"]["title"], " (", str(lstw["movie"]["year"]), ")"])
            media = lstw["type"]

          txt = item["label"]
          title = ""
          if item["type"] == "movie":
            txt = item["title"]
          elif item["type"] == "episode":
            txt = item["showtitle"]
            title = "- S{:02}E{:02}".format(item["season"], item["episode"])
          xbmc.log("Simkl: " + "; ".join([item["type"], txt, title]))
          interface.notify(getstr(32028).format(title), title=txt)
          r = 0

    except RuntimeError:
      pass
    except ZeroDivisionError:
      self.onPlayBackStopped()
