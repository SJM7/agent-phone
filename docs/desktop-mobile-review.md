# Desktop mobile review

The browser review recorder currently captures one Chromium tab. Device previews
are a separate testing surface: changing a desktop viewport does not turn the
browser into a mobile wallet's embedded browser.

## Useful desktop coverage

| Surface | What it can establish | What still needs checking |
| --- | --- | --- |
| Chromium narrow viewport | Responsive composition and ordinary browser interactions | Mobile engine and wallet behavior |
| Xcode iOS Simulator with Safari | Mobile Safari layout, keyboard, scrolling, and safe-area behavior | App Store wallet-specific integration |
| Android ARM64 Google Play emulator | Android browser and installable wallet-app flows | Each wallet's emulator compatibility and device-dependent features |
| Remote physical iPhone | Real iOS browser and compatible installed wallet flows | Service support and each wallet's restrictions |

Apple's supported Simulator installation path uses apps built for Simulator
through Xcode. Normal App Store wallet binaries are not a supported installation
path. Safari in Simulator is still valuable for web layout testing. See
[Apple's simulator workflow](https://developer.apple.com/documentation/Xcode/running-your-app-on-simulated-or-physical-devices)
and [the App Store-in-Simulator discussion](https://developer.apple.com/forums/thread/20257).

Android supports Apple silicon hosts and ARM64 system images. A Google Play AVD
includes the Play Store, which supplies the official Android versions of
Phantom, Solflare, and Backpack. App availability does not establish that a wallet
accepts an emulator: verify installation, launch, its dApp browser, connect,
deep-link return, and signing prompts individually using disposable test wallets.
See [emulator acceleration](https://developer.android.com/studio/run/emulator-acceleration)
and [Google Play AVDs](https://developer.android.com/studio/run/managing-avds).

For actual iOS wallet checks without keeping another phone on the desk, a remote
physical-device service is an option. BrowserStack App Live documents store-app
installation on real devices; that does not guarantee any particular wallet's
compatibility. Confirm the required dependent-app and wallet flows before
choosing a service. See [App Live](https://www.browserstack.com/docs/app-live).

## Relationship to Agent Phone

The Chromium extension does not run inside iOS Simulator Safari or native wallet
apps. Capturing those surfaces would require a separate simulator/device-screen
adapter. It could reuse bookmark times and the handoff format, but browser DOM
selectors and passive click metadata would not automatically be available.

A useful progression is to verify the page in a narrow Chromium viewport, check
Safari in the installed iOS Simulator, then trial the three Android wallets in
a Google Play AVD. Use a physical iOS device, local or remote, for remaining
wallet-specific checks. Capture and compare the same route, state, and source
revision across those surfaces; treat differences as evidence to investigate.
