on run
    try
        set bundlePath to POSIX path of (path to me)
        set projectPath to bundlePath & "/.."
        set launchCommand to "cd -P " & quoted form of projectPath & " && " & ¬
            "if [ -x ./.venv/bin/python ]; then " & ¬
            "exec ./.venv/bin/python ./launcher.py; " & ¬
            "else echo '找不到專案的 Python 環境，請保留 Lecture.app 在原本的專案資料夾內。' >&2; exit 1; fi"
        do shell script launchCommand
    on error messageText
        display alert "無法開啟課堂筆記" message messageText as critical buttons {"好"} default button "好"
    end try
end run
