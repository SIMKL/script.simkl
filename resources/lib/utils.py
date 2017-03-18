#!/usr/bin/python
# -*- coding: UTF-8 -*-
"""
Utils module. Some basic functions that maybe I'll need more than once
"""
import sys, os
import xbmc, xbmcaddon
__addon__ = xbmcaddon.Addon("script.simkl")

def getstr(strid):
    """ Given an id, returns the localized string """
    return __addon__.getLocalizedString(strid)

def getSetting(settingid):
    """ Given an id, return the setting """
    ret = __addon__.getSetting(settingid)
    xbmc.log("Simkl: {0}: {1}".format(settingid, ret))
    if ret == "false": ret = False
    elif ret == "true": ret = True
    return ret

def get_old_file(filename):
    """ Gets filename, returns full path """
    fullpath = os.path.join(xbmc.translatePath(__addon__.getAddonInfo("profile")).decode("utf-8"), "old_{}.json".format(filename))
    xbmc.log("Simkl: {} -- {}".format(filename, fullpath))
    return fullpath