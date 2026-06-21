-- FileMaker XML Compressor
-- Drop a "Save a Copy as XML" file on the app, or double-click to pick one.
-- Bundles fm_saxml_compress.py inside Contents/Resources.

property kScript : "fm_saxml_compress.py"

on run
	-- Launched without dropped files: prompt for the XML
	try
		set xmlFile to choose file with prompt "Select your FileMaker 'Save a Copy as XML' file:" of type {"public.xml", "xml"}
	on error number -128
		tell me to quit
		return -- user cancelled
	end try
	processFile(xmlFile)
	tell me to quit
end run

on open theseFiles
	repeat with f in theseFiles
		processFile(f)
	end repeat
	tell me to quit
end open

on processFile(xmlFile)
	set xmlPath to POSIX path of xmlFile

	-- Reject non-XML
	if xmlPath does not end with ".xml" then
		display dialog "Please select an XML file." buttons {"OK"} default button "OK" with icon stop
		return
	end if

	-- Default output location = parent folder of the XML
	set parentDir to do shell script "/usr/bin/dirname " & quoted form of xmlPath
	set parentAlias to (POSIX file parentDir) as alias

	try
		set outFolder to choose folder with prompt "Choose the folder to put the compressed files in:" default location parentAlias
	on error number -128
		return -- user cancelled
	end try

	-- Dump the output straight into the chosen folder — no subfolder.
	set outDir to POSIX path of outFolder

	-- Locate the bundled Python scripts
	set pyScriptPath to POSIX path of (path to resource kScript)
	set pyDir to do shell script "/usr/bin/dirname " & quoted form of pyScriptPath

	-- Build the command. Use system Python 3 (no extra packages needed).
	set cmd to "/usr/bin/env python3 " & quoted form of pyScriptPath & ¬
		" " & quoted form of xmlPath & ¬
		" --output-dir " & quoted form of outDir & ¬
		" 2>&1"

	try
		set output to do shell script cmd
	on error errMsg number errNum
		display dialog "Compression failed (exit " & errNum & "):" & return & return & errMsg buttons {"OK"} default button "OK" with icon stop
		return
	end try

	-- Reveal the output folder in Finder; no confirmation dialog.
	tell application "Finder"
		activate
		open (POSIX file outDir as alias)
	end tell
end processFile
