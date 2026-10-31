#!/bin/zsh
cd -- "${0:A:h}" || exit 1
/usr/bin/python3 ./export_notes.py
result=$?
read -r '?Press Return to close.'
exit "$result"
