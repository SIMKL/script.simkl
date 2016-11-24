#!/usr/bin/python
# -*- coding: UTF-8 -*-

import xbmc, xbmcaddon
import interface
import json

class Engine:
    def __init__(self, api, player):
        self.api = api
        self.player = player
        player.engine = self
        player.api    = api
        self.synclibrary()

    def synclibrary(self):
        ### UPLOAD ###

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
        xbmc.log("Simkl: Ret: {}".format(kodilibrary))
        kodilibrary = json.loads(kodilibrary)
        if kodilibrary["result"]["limits"]["total"] > 0:
            for movie in kodilibrary["result"]["movies"]:

                if movie["playcount"] > 0:
                    imdb = movie["imdbnumber"]
                    date = movie["lastplayed"]
                    self.api.watched(imdb, "movie", date)

class Player(xbmc.Player):
    def __init__(self):
        xbmc.Player.__init__(self)

    def getMediaType(self):
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
        self.onPlayBackStopped()
    def onPlayBackSeek(self, *args):
        self.onPlayBackStopped()
    def onPlayBackResumed(self):
        self.onPlayBackStopped()
    def onPlayBackStopped(self):
        try:
            movie = self.getVideoInfoTag()
            imdb  = movie.getIMDBNumber().strip(" ")
            fname = self.getPlayingFile()
            media = xbmc.executeJSONRPC(json.dumps({"jsonrpc": "2.0", "method": "Player.GetItem", 
                "params": { "properties": [ "showtitle", "streamdetails","title"]
                , "playerid": 1 }, "id": "VideoGetItem"}))
            media = json.loads(media)["result"]["item"]["type"]
            xbmc.log("Simkl: IMDb: {}".format(imdb))
            xbmc.log("Simkl: Genre: " + movie.getGenre())
            xbmc.log("Simkl: MediaType: " + str(media))
            #Scrobble from filename only for testing purposes or error of other methods

            percentage = 100 * self.getTime() / self.getTotalTime()
            pctconfig  = int(self.addon.getSetting("scr-pct"))
            
            if percentage > pctconfig:
                xbmc.log("Simkl: Ready to scrobble {}".format(movie.getTitle()))
                if imdb == "":
                    xbmc.log("Simkl: No imdb - Fname: {}".format(fname))
                    self.api.watched(fname, media)
                else:
                    xbmc.log("Simkl: IMDB: " + str(imdb))
                    self.api.watched(imdb, media)

        except RuntimeError:
            pass
