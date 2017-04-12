#!/usr/bin/python
# -*- coding: UTF-8 -*-
"""
Utils module. Some basic functions that maybe I'll need more than once
"""
import sys, os
import xbmc, xbmcaddon
from datetime import datetime
__addon__ = xbmcaddon.Addon("script.simkl")

KODI_TIME_FORMAT = "%Y-%m-%d %H:%M:%S"
SIMKL_TIME_FORMAT = "%Y-%m-%dT%H:%M:%S.000Z"

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
    fullpath = os.path.join(xbmc.translatePath(__addon__.getAddonInfo("profile")).decode("utf-8"),\
        "old_{}.json".format(filename))
    xbmc.log("Simkl: {} -- {}".format(filename, fullpath))
    return fullpath

def simkl_time_to_kodi(string):
    """ gets simkl time and returns kodi time """
    return datetime.strptime(string, SIMKL_TIME_FORMAT).strftime(KODI_TIME_FORMAT)
def kodi_time_to_simkl(string):
    """ gets kodi time and returns simkl time """
    return datetime.strptime(string, KODI_TIME_FORMAT).strftime(SIMKL_TIME_FORMAT)

def simkl2kodi(objects):
    """ Simkl dictionary format to kodi library format """
    watched_movies_list = []
    for movie in objects:
        if movie["status"] == "completed":
            watched_movies_list.append({
                "year": movie["movie"]["year"],
                "imdbnumber": movie["movie"]["ids"]["imdb"],
                "label": movie["movie"]["title"],
                "playcount": 1,
                "lastplayed": simkl_time_to_kodi(movie["last_watched_at"]),
            })

    return watched_movies_list

def find_match(item, database):
    """ returns match from database """
    for item_database in database:
        if item["imdbnumber"] == item_database["imdbnumber"]: return item_database
    return None

def compare_max(tuple1, tuple2):
    """ Pretty simple, no comments """
    if tuple1[1] >= tuple2[1]:
        return tuple1[0]
    return tuple2[0]
