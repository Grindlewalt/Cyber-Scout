Cyber-Scout downloads. Install notes: client/README.md and deploy/README.md in the source.

  cyberscout-client-windows-x64.exe   Cyber-Scout Client, Windows 10/11 x64 (unsigned: SmartScreen > More info > Run anyway)
  cyberscout-client-macos-arm64       Cyber-Scout Client, macOS Apple silicon (unsigned: chmod +x, then right-click > Open once)
  cyberscout-client-macos-x64         Cyber-Scout Client, macOS Intel (unsigned: chmod +x, then right-click > Open once)
  cyberscout-client-linux-x64         Cyber-Scout Client, Linux x86_64 (chmod +x)
  cyberscout-android.apk              Android app
  install.sh                          server installer, Linux and macOS
  install.ps1                         server installer, Windows
  get.sh                              one-line server install from this site
  get.ps1                             one-line server install from this site
  cyberscout-src.tar.gz               the source code at this version

Help process matches (in the client: http://127.0.0.1:7600/client/processing, admins): tracking needs an
NVIDIA GPU (Windows / Linux; GTX 16xx / RTX 20xx or newer, driver 580+) or Apple silicon (macOS 14+).
"Set up processing" downloads its runtime once into the client's data folder: about 1.8 GB for NVIDIA,
0.25 GB on a Mac. Any computer can run Clip only (record the stream, send the server match clips).

download.html links to exactly these names; SHA256SUMS has their checksums.
