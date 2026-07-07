#!/bin/bash

if [ -z "$1" ]; then
  echo "Usage: $0 <output_file.zip>"
  exit 1
fi

zip -r "$1" . -x "*.git*"