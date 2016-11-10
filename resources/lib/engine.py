#!/usr/bin/python
# -*- coding: UTF-8 -*-

import xbmc, xbmcaddon
import interface

class Engine:
    def __init__(self, api, player):
        self.api = api
        self.player = player
        player.engine = self
        player.api    = api

class Player(xbmc.Player):
    def __init__(self):
        xbmc.Player.__init__(self)

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
            xbmc.log("Simkl: VideoInfoTag: " + str(movie))
            #Scrobble from filename only for testing purposes or error of other methods

            percentage = 100 * self.getTime() / self.getTotalTime()
            pctconfig  = int(self.addon.getSetting("sct-w-pct"))
            
            if percentage > pctconfig:
                xbmc.log("Simkl: Ready to scrobble {}".format(movie.getTitle()))
                if imdb == "":
                    xbmc.log("Simkl: No imdb - Fname: {}".format(fname))
                    self.api.checkin(fname)
                else:
                    xbmc.log("Simkl: IMDB: " + str(imdb))
                    self.api.checkin(imdb)

        except RuntimeError:
            pass