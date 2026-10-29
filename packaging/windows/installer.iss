; Inno Setup Script para Advanced Timegrapher en Windows (x64)
; Compilar con:
;   iscc /DMyAppVersion=0.7.0 packaging\windows\installer.iss
; o utilizando el script auxiliar de PowerShell / GitHub Actions.

#ifndef MyAppVersion
#define MyAppVersion "0.7.0"
#endif

#define MyAppName "Advanced Timegrapher"
#define MyAppPublisher "Alberto Pico Lara"
#define MyAppURL "https://github.com/SPQR14/advanced_timegrapher"
#define MyAppExeName "adv-tg.exe"

[Setup]
AppId={{F8D7653A-7B93-4B9E-9A52-82845347B01A}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
AppPublisherURL={#MyAppURL}
AppSupportURL={#MyAppURL}
AppUpdatesURL={#MyAppURL}
DefaultDirName={autopf}\{#MyAppName}
DefaultGroupName={#MyAppName}
AllowNoIcons=yes
OutputDir=..\..\dist
OutputBaseFilename=adv-tg-windows-x64-setup
SetupIconFile=adv-tg.ico
Compression=lzma2/ultra64
SolidCompression=yes
WizardStyle=modern
ArchitecturesInstallIn64BitMode=x64compatible
; Permite instalación para el usuario actual sin requerir elevación de administrador,
; pero si se ejecuta como administrador se instala en Program Files para todos los usuarios.
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=commandline dialog
UninstallDisplayIcon={app}\{#MyAppExeName}
VersionInfoVersion={#MyAppVersion}

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"
Name: "spanish"; MessagesFile: "compiler:Languages\Spanish.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[Files]
Source: "..\..\dist\adv-tg\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; IconFilename: "{app}\{#MyAppExeName}"
Name: "{group}\{cm:UninstallProgram,{#MyAppName}}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon; IconFilename: "{app}\{#MyAppExeName}"

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "{cm:LaunchProgram,{#StringChange(MyAppName, '&', '&&')}}"; Flags: nowait postinstall skipifsilent
