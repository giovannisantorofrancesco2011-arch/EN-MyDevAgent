; MyDevAgent Studio (English edition): Windows installer (Inno Setup 6). Compiled by studio\build.ps1 with:
;   ISCC /DStudioVersion=0.2.0 /DStudioSource=<modified VSCodium> /DExe=VSCodium.exe /DBranding=<icons>
; It has its own AppId, folder, shortcuts and registry keys, so it can be installed next to the Italian edition.
#define StudioName "MyDevAgent Studio"
#define StudioEdition "MyDevAgent Studio EN"

[Setup]
AppId={{BE5E880B-4F27-402B-87C5-C58F7E660D2B}
AppName={#StudioName}
AppVersion={#StudioVersion}
AppVerName={#StudioName} {#StudioVersion}
AppPublisher=MyDevAgent
AppPublisherURL=https://github.com/giovannisantorofrancesco2011-arch/MyDevAgent
AppSupportURL=https://github.com/giovannisantorofrancesco2011-arch/MyDevAgent/issues
DefaultDirName={localappdata}\Programs\{#StudioEdition}
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputBaseFilename=MyDevAgent-Studio-Setup-EN
SetupIconFile={#Branding}\vio.ico
UninstallDisplayIcon={app}\{#Exe}
UninstallDisplayName={#StudioName} (English)
WizardStyle=modern
WizardImageFile={#Branding}\wizard.bmp,{#Branding}\wizard-2x.bmp
WizardSmallImageFile={#Branding}\wizard-small.bmp,{#Branding}\wizard-small-2x.bmp
Compression=lzma2/max
SolidCompression=yes
CloseApplications=force

[Languages]
Name: "en"; MessagesFile: "compiler:Default.isl"

[Messages]
en.WelcomeLabel2=This will install [name/ver]: the code editor with Vio, your coding agent that works on your own computer, with no cloud.%n%nIf they are missing, I can also install Python, Ollama and MyDevAgent.

[Tasks]
Name: "mydevagent"; Description: "Also install Python, Ollama and MyDevAgent if they are missing (needs Internet: the models take a few GB)"
Name: "desktopicon"; Description: "Create a desktop icon"; GroupDescription: "Icons:"
Name: "contextmenu"; Description: "Add ""Open with {#StudioName}"" to the folder menu"; GroupDescription: "Other:"

[InstallDelete]
; remove the files of the previous version (VSCodium can change its layout between versions)
Type: filesandordirs; Name: "{app}\resources"
Type: filesandordirs; Name: "{app}\locales"
Type: filesandordirs; Name: "{app}\bin"
Type: filesandordirs; Name: "{app}\extras"

[Files]
Source: "{#StudioSource}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\{#StudioEdition}"; Filename: "{app}\{#Exe}"; AppUserModelID: "MyDevAgent.Studio.EN"
Name: "{autodesktop}\{#StudioEdition}"; Filename: "{app}\{#Exe}"; AppUserModelID: "MyDevAgent.Studio.EN"; Tasks: desktopicon

[Registry]
Root: HKCU; Subkey: "Software\Classes\Directory\shell\MyDevAgentStudioEN"; ValueType: expandsz; ValueName: ""; ValueData: "Open with {#StudioName}"; Tasks: contextmenu; Flags: uninsdeletekey
Root: HKCU; Subkey: "Software\Classes\Directory\shell\MyDevAgentStudioEN"; ValueType: expandsz; ValueName: "Icon"; ValueData: "{app}\{#Exe}"; Tasks: contextmenu
Root: HKCU; Subkey: "Software\Classes\Directory\shell\MyDevAgentStudioEN\command"; ValueType: expandsz; ValueName: ""; ValueData: """{app}\{#Exe}"" ""%V"""; Tasks: contextmenu
Root: HKCU; Subkey: "Software\Classes\Directory\Background\shell\MyDevAgentStudioEN"; ValueType: expandsz; ValueName: ""; ValueData: "Open with {#StudioName}"; Tasks: contextmenu; Flags: uninsdeletekey
Root: HKCU; Subkey: "Software\Classes\Directory\Background\shell\MyDevAgentStudioEN"; ValueType: expandsz; ValueName: "Icon"; ValueData: "{app}\{#Exe}"; Tasks: contextmenu
Root: HKCU; Subkey: "Software\Classes\Directory\Background\shell\MyDevAgentStudioEN\command"; ValueType: expandsz; ValueName: ""; ValueData: """{app}\{#Exe}"" ""%V"""; Tasks: contextmenu

[Run]
Filename: "powershell.exe"; Parameters: "-NoProfile -ExecutionPolicy Bypass -File ""{app}\extras\install-mydevagent.ps1"" -Pause"; StatusMsg: "Installing Python, Ollama and MyDevAgent: follow the window that just opened..."; Tasks: mydevagent; Flags: waituntilterminated
Filename: "{app}\{#Exe}"; Description: "Launch {#StudioName}"; Flags: nowait postinstall skipifsilent
