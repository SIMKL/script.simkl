#!/usr/bin/python
# -*- coding: UTF-8 -*-
'''
NOT LICENSED YET
Creator: David Davó <david@ddavo.me>
'''

import time, sys
import xbmcaddon
import xbmc

simkl_addon = xbmcaddon.Addon()
autoscrobble = simkl_addon.getSetting("autoscrobble")

if __name__ == "__main__":
    monitor = xbmc.Monitor()
    player  = xbmc.Player()
    #player.onPlayBackStarted() #Now call a function that marks movie as "watching" or serie as "tracking"
    #Remember: if getTime() is more than x% scrobble file

    while not monitor.abortRequested():
        if monitor.waitForAbort(10):
            break
        elif player.isPlaying():
            xbmc.log("Simkl:" + player.getPlayingFile(), level=xbmc.LOGDEBUG)