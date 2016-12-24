#!/usr/bin/python
# -*- coding: UTF-8 -*-

import xbmc, xbmcaddon
import interface
import json
__addon__ = interface.__addon__
def getstr(id): return interface.getstr(id)

class Engine:
    def __init__(self, api, player):
        self.api = api
        self.player = player
        player.engine = self
        player.api    = api
        self.synclibrary()

    def synclibrary(self):
        ### UPLOAD ###
        #DISABLED UNTIL WORKING FINE
        pass
        '''
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
                #Dont do that, upload all at once

                if movie["playcount"] > 0:
                    imdb = movie["imdbnumber"]
                    date = movie["lastplayed"]
                    self.api.watched(imdb, "movie", date)
        '''

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
        #self.onPlayBackStopped()
        pass
    def onPlayBackResumed(self):
        #self.onPlayBackStopped()
        pass
    def onPlayBackEnded(self):
        xbmc.log("Simkl: ONPLAYBACKENDED")
        self.onPlayBackStopped()
    def onPlayBackStopped(self):
        try:
            movie = self.getVideoInfoTag()
            imdb  = movie.getIMDBNumber().strip(" ")
            fname = self.getPlayingFile()
            thing = xbmc.executeJSONRPC(json.dumps({"jsonrpc": "2.0", "method": "Player.GetItem", 
                "params": { "properties": [ "showtitle", "streamdetails","title"]
                , "playerid": 1 }, "id": "VideoGetItem"}))
            media = json.loads(thing)["result"]["item"]["type"]
            xbmc.log("Simkl: IMDb: {}".format(imdb))
            xbmc.log("Simkl: Genre: " + movie.getGenre())
            xbmc.log("Simkl: MediaType: " + str(media))
            #Scrobble from filename only for testing purposes or error of other methods

            percentage = 100 * self.getTime() / self.getTotalTime()
            pctconfig  = int(self.addon.getSetting("scr-pct"))
            
            if percentage > pctconfig:
                bubble = __addon__.getSetting("bubble")

                xbmc.log("Simkl: Ready to scrobble {}".format(movie.getTitle()))
                if imdb == "":
                    xbmc.log("Simkl: No imdb - Fname: {}".format(fname))
                    r = self.api.watched(fname, media) #r from response
                else:
                    xbmc.log("Simkl: IMDB: " + str(imdb))
                    r = self.api.watched(imdb, media)

                if bubble and r:
                    xbmc.log("Simkl: Full: {}".format(thing))
                    interface.notify(getstr(32028).format(
                        json.loads(thing)["result"]["item"]["label"]))

        except RuntimeError:
            pass
