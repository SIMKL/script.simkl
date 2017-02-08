#!/bin/bash
#Creates a zip file compatible with Kodi

if [ "$1" == "zip" ]; then
    date --rfc-3339=seconds > resources/data/compdate.txt
    mkdir script.simkl
    rm script.simkl.zip

    rsync -rv --progress ./ ./script.simkl --exclude-from .gitignore --exclude tozip.sh
    zip -rx@.gitignore script.simkl.zip script.simkl/* -x script.simkl/script.simkl/
    rm -Rf script.simkl
elif [ "$1" == "pull" ]; then
    #Remember. It should've been checkout before
    REPO="/home/$USER/Documentos/repo-scripts/script.simkl"
    rm -Rf $REPO/* $REPO/.*
    rsync -rv --progress ./ $REPO --exclude-from .gitignore --exclude tozip.sh --exclude script.simkl.zip --exclude ".git*"
    echo
    ls -a --color=auto $REPO
elif [ "$1" == "bak" ]; then
    #Workaround. Use rsync or unison first pls
    BAKDATE="$(date '+%F %T')"
    echo $BAKDATE
    zip -r ".bak/$BAKDATE" ./* -x .bak
else
    echo "$1 Does nothing"
fi
exit