Cyber-Scout downloads (code 529bbde). Install notes: client/README.md and deploy/README.md in the source.

One-line server install (works where http://10.0.0.58:7580 is reachable; downloads the code and the model from it):
  Linux / macOS:        curl -fsSL http://10.0.0.58:7580/downloads/get.sh | bash
  Windows (PowerShell): irm http://10.0.0.58:7580/downloads/get.ps1 | iex

One-line Client install (installs, starts at login, opens it; connects to http://10.0.0.58:7560):
  macOS / Linux:        curl -fsSL http://10.0.0.58:7580/downloads/get-client.sh | bash
  Windows (PowerShell): irm http://10.0.0.58:7580/downloads/get-client.ps1 | iex

  cyberscout-client-windows-x64.exe   Cyber-Scout Client, Windows 10/11 x64 (unsigned: the browser may ask to Keep it; SmartScreen > More info > Run anyway)
  cyberscout-client-macos-arm64       Cyber-Scout Client, macOS Apple silicon (unsigned: see macOS below)
  cyberscout-client-macos-x64         Cyber-Scout Client, macOS Intel (unsigned: see macOS below)
  cyberscout-client-linux-x64         Cyber-Scout Client, Linux x86_64 (chmod +x, then run it)
  cyberscout-android.apk              Android app (debug-signed: allow this source; Play Protect > More details > Install anyway)
  install.sh                          server installer, Linux and macOS
  install.ps1                         server installer, Windows
  get.sh                              one-line server install, Linux and macOS (checks the installer and code SHA-256)
  get.ps1                             one-line server install, Windows (checks the installer and code SHA-256)
  get-client.sh                       one-line Client install, macOS and Linux (checks the build's SHA-256; starts at login)
  get-client.ps1                      one-line Client install, Windows (checks the build's SHA-256; starts at sign-in)
  robot.onnx                          the robot detector model (the installers download it with --model / -Model)
  cyberscout-src.tar.gz               the source code at this version
  SHA256SUMS                          SHA-256 of the files above

macOS Client by hand (the one-liner above skips all of this): in Terminal (Intel Macs: -x64, not -arm64):
  cd ~/Downloads
  xattr -d com.apple.quarantine cyberscout-client-macos-arm64
  chmod +x cyberscout-client-macos-arm64
  ./cyberscout-client-macos-arm64
If macOS still blocks it: System Settings > Privacy & Security > Open Anyway (right-click > Open no longer
does this on macOS 15).

Help process matches (in the client: http://127.0.0.1:7600/client/processing, admins): tracking needs an
NVIDIA GPU (Windows / Linux; GTX 16xx / RTX 20xx or newer, driver 580+) or Apple silicon (macOS 14+).
"Set up processing" downloads its runtime once into the client's data folder: about 1.8 GB for NVIDIA,
0.25 GB on a Mac. Any computer can run Clip only (record the stream, send the server match clips).

download.html links to exactly these names; SHA256SUMS has their checksums.
