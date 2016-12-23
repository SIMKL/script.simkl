#!/bin/bash
#Creates a zip file compatible with Kodi

mkdir script.service.simkl
rsync -rv --progress ./ ./script.service.simkl --exclude-from .gitignore --exclude tozip.sh

rm script.service.simkl.zip
zip -rx@.gitignore script.service.simkl.zip script.service.simkl/*
rm -Rf script.service.simkl
