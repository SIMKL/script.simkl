#!/usr/bin/python
# -*- coding: UTF-8 -*-

import os
import xbmc
import interface
from utils import *
import json
__addon__ = interface.__addon__
def getstr(strid): return interface.getstr(strid)

class Engine:
  def __init__(self, api, player):
    self.api = api
    self.player = player
    player.engine = self
    player.api    = api

  def synclibrary(self, mode="full"):
    simkl_old = get_old_file("simkl")
    kodi_old = get_old_file("kodi")
    kodi_current = {"movies": self.get_movies(), "episodes": self.get_episodes()}
    if not os.path.exists(old_kodi):
    #if True:
      with open(kodi_old, "w+") as f:
        f.write(json.dumps(kodi_current, indent=2))

  @staticmethod
  def get_movies():
    movies_list = []
    movies = json.loads(xbmc.executeJSONRPC(json.dumps({
          "jsonrpc": "2.0",
          "method": "VideoLibrary.GetMovies",
          "params": {
            "limits": {
              "start": 0,
              "end": 0
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
      })))["result"]["movies"]
    for movie in movies:
      if movie["playcount"] > 0:
        movies_list.append(movie)
    return movies_list

  @staticmethod
  def get_episodes():
    shows_library = json.loads(xbmc.executeJSONRPC(json.dumps({
      "jsonrpc": "2.0",
      "method": "VideoLibrary.GetTVShows",
      "params": {
        "limits": {"start": 0, "end": 0},
        "properties": ["imdbnumber", "title", "watchedepisodes", "year"],
        "sort": {"order": "ascending", "method": "season"}
      },
      "id": "libMovies"
    })))
    if shows_library["result"]["limits"]["total"] == 0: return None
    list_tvshows = []
    for tvshow in shows_library["result"]["tvshows"]:
      list_episodes = []
      episodes = json.loads(xbmc.executeJSONRPC(json.dumps({
        "jsonrpc": "2.0",
        "method": "VideoLibrary.GetEpisodes",
        "params": {
          "limits": {"start": 0, "end": 0},
          "properties": ["playcount", "season", "episode", "lastplayed", "tvshowid"],
          "sort": {"order": "ascending", "method": "playcount"},
          #"season": season["season"],
          "tvshowid": tvshow["tvshowid"]
        },
        "id": tvshow["tvshowid"]
      })))

      if episodes["result"]["limits"]["end"] > 0:
        for episode in episodes["result"]["episodes"]:
          if episode["playcount"] > 0: list_episodes.append(episode)
      if len(list_episodes) > 0:
        list_tvshows.append({
          "title":tvshow["title"],
          "year":tvshow["year"],
          "imdbnumber":tvshow["imdbnumber"],
          "episodes":list_episodes})

    return list_tvshows

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
    ''' Gets the info needed to pass to the api '''
    self.api.check_connection()
    try:
      item = json.loads(xbmc.executeJSONRPC(json.dumps({
        "jsonrpc": "2.0", "method": "Player.GetItem",
        "params": {
          "properties": ["showtitle", "title", "season", "episode", "file", "tvshowid", "imdbnumber", "year" ],
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
            txt = "".join([item["title"], " (", str(item["year"]), ")"])
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
