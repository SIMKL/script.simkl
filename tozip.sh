#!/bin/bash
#Creates a zip file compatible with Kodi

mkdir script.simkl
rsync -rv --progress ./ ./script.simkl --exclude-from .gitignore --exclude tozip.sh

rm script.simkl.zip
zip -rx@.gitignore script.simkl.zip script.simkl/*
rm -Rf script.simkl
