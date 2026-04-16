-- FileMaker XML Compressor
-- Drop a "Save a Copy as XML" file on the app, or double-click to pick one.
-- Bundles fm_saxml_compress.py inside Contents/Resources.

property kScript : "fm_saxml_compress.py"

on run
	-- Launched without dropped files: prompt for the XML
	try
		set xmlFile to choose file with prompt "Select your FileMaker 'Save a Copy as XML' file:" of type {"public.xml", "xml"}
	on error number -128
		return -- user cancelled
	end try
	processFile(xmlFile)
end run

on open theseFiles
	repeat with f in theseFiles
		processFile(f)
	end repeat
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
		set outFolder to choose folder with prompt "Where should the compressed output folder go?" default location parentAlias
	on error number -128
		return -- user cancelled
	end try
	set outRoot to POSIX path of outFolder

	-- Build a unique subfolder name based on the XML filename
	set xmlBase to do shell script "/usr/bin/basename " & quoted form of xmlPath & " .xml"
	set outDir to outRoot & xmlBase & "_Compressed"

	-- If it already exists, append a timestamp
	set existsCheck to do shell script "[ -d " & quoted form of outDir & " ] && echo yes || echo no"
	if existsCheck is "yes" then
		set ts to do shell script "/bin/date +%Y%m%d-%H%M%S"
		set outDir to outDir & "_" & ts
	end if

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

	-- Pull the last few lines of output for the success dialog
	set tailLines to do shell script "/usr/bin/tail -n 6 <<'EOF'
" & output & "
EOF"

	set userChoice to button returned of (display dialog "DDR compression complete." & return & return & tailLines buttons {"Show in Finder", "Done"} default button "Show in Finder" with icon note)

	if userChoice is "Show in Finder" then
		tell application "Finder"
			activate
			open (POSIX file outDir as alias)
		end tell
	end if
end processFile
