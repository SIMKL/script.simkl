#!/usr/bin/python
# -*- coding: UTF-8 -*-
"""
Utils module. Some basic functions that maybe I'll need more than once
"""
import sys
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
    return ret