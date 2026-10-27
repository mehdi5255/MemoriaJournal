# Lance les tests avec la couverture
Set-Location "$PSScriptRoot\MemoriA"
& "$PSScriptRoot\env\Scripts\coverage.exe" run manage.py test
& "$PSScriptRoot\env\Scripts\coverage.exe" report
