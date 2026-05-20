#!/bin/bash
ssh user@pop-os rsync -av --include="*.txt" --exclude="*" ftp@ftp.ibiblio.org::gutenberg ~/data/gutenberg/
