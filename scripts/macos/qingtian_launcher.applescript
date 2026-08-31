on run
	set supportRoot to (POSIX path of (path to application support from user domain)) & "QingTian"
	set appDir to supportRoot & "/runtime"
	set dataDir to supportRoot & "/data"
	set serverPort to "8000"

	try
		do shell script "cd " & quoted form of appDir & " && PATH=/usr/local/bin:/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin API_KEYS=$(/usr/bin/openssl rand -hex 32) QINGTIAN_DATA_DIR=" & quoted form of dataDir & " HOST=127.0.0.1 PORT=" & serverPort & " ./scripts/restart_server.sh"
	on error errMsg number errNum
		display dialog ("青天评标启动失败 (" & errNum & "):" & return & errMsg & return & return & "请查看日志: " & appDir & "/build/server.log") buttons {"OK"} default button "OK" with icon stop
		return
	end try

	try
		open location ("http://127.0.0.1:" & serverPort & "/")
	end try
end run
