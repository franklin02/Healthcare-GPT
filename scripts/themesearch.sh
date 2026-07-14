#!/bin/bash

clear
echo === GDELT THEME SEARCH ===
while :
do
	if [ ! -f ./LOOKUP-GKGTHEMES.TXT ];
	then
		echo Downloading LOOKUP_GKGTHEMES.TXT...
		curl -O http://data.gdeltproject.org/api/v2/guides/LOOKUP-GKGTHEMES.TXT
		echo Donwload complete
	fi
	echo Input search term:
	read theme
	clear
	echo === SEARCH RESULTS FOR $theme ===
	grep -i $theme LOOKUP-GKGTHEMES.TXT
done
