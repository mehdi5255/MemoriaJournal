# Lance le serveur Django (sans avoir à activer env)
Set-Location "$PSScriptRoot\MemoriA"
& "$PSScriptRoot\env\Scripts\python.exe" manage.py runserver @args
