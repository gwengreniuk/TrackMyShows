# TrackMyShows

A Kodi 21 add-on that remembers what you've watched across all your Kodi boxes and tells you where you left off. It's a replacement for Trakt watch tracking, with your history stored in your own Google Drive.

- Runs quietly in the background and notices anything you play: Seren, Premiumize cloud files, local files.
- An episode or movie counts as **watched** once you pass 85% of it (adjustable). Partial watches show up under **In progress**.
- **Where was I?** lists every show with its next unwatched episode that has already aired.
- History syncs through a `TrackMyShows` folder in your Google Drive. The add-on can only see files it created itself, never the rest of your Drive.
- If a file name can't be identified, it goes into **Needs attention**, where you fix it once.

## One-time setup (on your PC)

### 1. TMDb API key (free, about 2 minutes)
1. Create an account at https://www.themoviedb.org and verify your email.
2. Go to **Settings → API → Create → Developer**, fill in the short form (personal use is fine), and accept.
3. Copy the **API Key** (the short one). Save it as `secrets/tmdb_api_key.txt`.

### 2. Google Cloud OAuth client (about 10 minutes)
1. Go to https://console.cloud.google.com and create a project named `TrackMyShows`.
2. **APIs & Services → Library**: search for **Google Drive API** and click **Enable**.
3. **Google Auth Platform** (or **OAuth consent screen**) → **Get started**:
   - App name `TrackMyShows`, with your email as the support and developer contact.
   - Audience: **External**.
4. **Data Access → Add or remove scopes**: add `.../auth/drive.file` and save. It's a non-sensitive scope, so Google doesn't need to review the app.
5. **Clients → Create client**:
   - Application type: **TVs and Limited Input devices**, name `Kodi`.
   - Click **Download JSON** and save the file into `secrets/` (any `*.json` name works).
6. **Switch to In production** (do not skip this):
   - Go to **Google Auth Platform → Audience**. You'll see *Publishing status: Testing*.
   - Click **Publish app → Confirm**. The status changes to **In production**.
   - Why it matters: in Testing mode, Google expires sign-ins after **7 days**, so every box would need signing in again each week. In production, sign-ins last indefinitely.
   - Since you only use `drive.file`, no verification is required. When you sign in, Google may show "Google hasn't verified this app". Click **Advanced → Go to TrackMyShows (unsafe)**. It's your own app, so that's expected.

### 3. Build the add-on
```
python build.py --serve
```
This creates `dist/plugin.video.trackmyshows-0.1.0.zip` with your keys built in, then serves it on your network and prints an address like `http://192.168.1.20:8089/`. If Windows Firewall asks, allow access on private networks.

## Install on each Kodi box
1. **Settings → System → Add-ons → Unknown sources: On.**
2. **Settings → File manager → Add source**. Enter the address printed above and name it `tms`.
3. **Add-ons → Install from zip file → tms →** choose the zip.
4. Open **Add-ons → Video add-ons → TrackMyShows → Sign in to Google…**. On your phone, go to the address shown and enter the code.

Repeat on each box; it takes a couple of minutes per box. Every box syncs every 15 minutes, and also shortly after you finish something.

## Using it
- **Where was I?**: your shows with the next episode to watch. Long-press or open the context menu to mark that episode watched or hide the show.
- **TV shows → show → season**: every episode, with watched ticks. Select an episode to mark it watched or unwatched, mark everything **up to here**, or mark a whole season.
- **Search & mark watched…**: quickly fill in history you lost from Trakt. Find the show, open the season where you are, and use "Mark watched up to here".
- **Needs attention**: videos the add-on couldn't identify. Pick one, search for the title, and enter the season and episode.

**Playing in Seren:** selecting a show in **Where was I?** starts its next episode in Seren, which finds sources as usual. The context menu has **Play in Seren**, **Open in Seren** and **Browse episodes**. Episode menus elsewhere also offer **Play in Seren**. You can change what selecting a show does under **Settings → General**.
If Seren's own copy of a show is out of date and missing a newer episode, which happens when its Trakt syncing isn't working, the add-on notices and asks Seren to refresh that show before playing.

To play an episode directly, the add-on needs the show's Trakt ID. It learns that automatically the first time you watch the show through Seren, and also checks Seren's own list of known shows. For a show added from the phone that Seren hasn't seen yet, it opens Seren's search instead. Pick the show once; after you watch an episode it plays directly.

**Choosing the source yourself:** use **Choose source in Seren** from the context menu to get Seren's usual "Select Source" window instead of autoplay. This helps when Seren picks the wrong version of a show, such as a 2008 remake instead of the 1982 original. To make it the default, set **Settings → General → Selecting a show** to *Play next episode in Seren, choosing the source*.

**Premiumize first:** playing a show (Where was I?, **Play**, or ▶ from the phone) uses your Premiumize cloud whenever it has the episode. If the show isn't linked yet, the add-on searches your cloud for a folder with the show's name. A folder naming a different year is never used, so a 2008 remake's folder can't match the 1982 original. If a folder matches, it's linked automatically. Only when Premiumize doesn't have the episode does it go to Seren.

**Premiumize folders:** a show can be linked to a folder in your Premiumize cloud, for example *My Show (1982) Complete Series*. **Where was I?** then plays the next episode straight from that folder: the add-on looks through the folder and any season subfolders and matches file names like `S02E05`. There are two ways to link:
- **Automatically:** play an episode from the show's folder in Seren's **My Files → Premiumize** or in the official Premiumize add-on, or let it be found by searching your cloud, as described above.
- **Manually:** context menu → **Premiumize folder… → Link a Premiumize folder…** searches your cloud by title.

Links sync to all your boxes. The add-on uses the Premiumize login Seren already has.

Tip: add **Where was I?** to your home menu. Open it, then use the context menu and choose **Add to favourites**.

## How sync works
Each box writes only its own file (`events-<device>.jsonl`) in your Drive's `TrackMyShows` folder, and reads every other box's file. History is an append-only list of events with unique IDs, so merging is conflict-free. Boxes never overwrite each other, and an offline box catches up later. Each box also keeps a full local copy, so tracking works without the internet; it syncs when it can.

## Phone app (Android)

The `phone/` folder is a web app you install from Chrome. It reads and writes the same Drive history as the Kodi boxes.

- **Watching**: every active show with its next episode and a progress bar. Switch between **Cards** and **Compact** (one line per show) at the top; tap a show to open its page. Tap **✓** to mark that episode watched; an Undo appears for a few seconds.
- **New**: new seasons and episodes of your active shows, plus air dates coming up. The app checks TMDb each time you open it.
- **Movies**: search for movies and **Add** them to your list. Work through **To watch** with ▶ (play on TV) and ✓ (watched); tap a movie for more options. **Watched** includes movies Kodi tracked automatically. In Kodi the same list appears as **TrackMyShows → Movies to watch**. Playing a movie uses your Premiumize cloud first, matched by title and year, then Seren.
- **Discover**: browse **Trending TV**, **In theatres**, **Upcoming movies**, **Top rated TV** and **Top rated movies** (highly rated titles of any age). **Add** follows a show or puts a movie on your list. **Remove** hides a title from these lists for good, whether you've seen it or it doesn't interest you; an Undo appears briefly. Anything you already follow, have listed or have watched never appears.
- **Origin** (Discover → ⚙ Filters): Discover starts with US, Canadian and British productions. Switch to **English-language** or **All countries** any time; the choice is remembered. Search boxes always search worldwide.
- **Filters** (Discover → ⚙ Filters): every filter is three-way. Tap once for **with** (✓), twice for **without** (✕), three times for off. Covers genres; movie age ratings (G, PG, PG-13, R, NC-17, Not rated; "without PG" means every other rating); and content tags (smoking, violence, drugs, nudity & sex, suicide, sexual assault and more), so you can look for something specifically with or without nudity. There's also a minimum rating. Content tags come from TMDb's community tagging, so they catch a lot but not everything. Several "with" tags match titles that have **any** of them. Filters apply to every Discover list and are remembered.
- **Summaries**: tap any movie for its page, with poster, runtime, genres, plot and where it streams. Show pages show the plot too; tap it to expand. Tap an episode to open its page: plot, air date, runtime, rating, director and writers, guest stars, **Play on TV**, **Watched**, **Mark watched up to here**, and Previous/Next episode.
- **Open in Netflix / Prime Video / Crave / BritBox on TV**: on show and movie pages, services from "Watch on" get a button that opens that app on your TV. BritBox and Crave open in Prime Video, because they're subscribed through Amazon as Prime Video Channels. When Wikidata knows the title's Netflix or Prime Video ID, the app opens straight to the title; otherwise it opens that app's search for the title. Kodi must be running on the TV, even in the background, and the app must be installed on it.
- **Ratings next to titles**: everywhere titles appear, you see the TMDb star score and the age rating, such as ★ 7.9 18A. The age rating is your region's when TMDb has one, otherwise the US rating.
- **Copy settings to a new device**: Settings → **Show QR code** on a set-up device, then scan it with the new device's camera (or **Scan QR code** in the app) and tap **Import**. The keys travel in the link's `#` part, which is never sent to a server, and nothing is stored in the repo.
- **Flip cover screen**: on a cover-sized screen the app shows two tabs. **Up next** lists your next episodes with large ▶ (play on TV) and ✓ (watched) buttons. **Remote** has a compact d-pad with Back, Home, play/pause and volume. Run it on the Flip's cover screen with Good Lock → MultiStar. Long-press the app icon for shortcuts to Up next, Discover and Movies.
- **TV remote (🎮)**: a full remote in the app: d-pad, OK, Back, Home, Menu, Info, play/pause, skip, volume, and instant typing into Kodi's keyboard. It works on home Wi-Fi through a small secure server in the TrackMyShows Kodi add-on (port 8443). It's protected by a secret key that the app reads from your private Drive, and Kodi's own web-server password stays on. The first time on each phone, tap **Allow connection** and accept Chrome's certificate warning. Away from home, typing still works through Drive.
- **Type on TV (⌨)**: open a search box in Kodi, tap ⌨ in the phone app's top bar, type, and tap **Send to TV**. The text goes into Kodi's keyboard and, optionally, presses Done. It arrives within about 6–10 seconds.
- **Cast**: show, movie and episode pages list the cast. Tap anyone to see their photo, biography and everything they've been in (All / TV / Movies). Titles you follow, have listed or have watched are marked, and tapping one opens its page.
- **Rotten Tomatoes**: add a free OMDb key (omdbapi.com/apikey.aspx) in Settings to show 🍅 Rotten Tomatoes, IMDb and Metacritic scores on movie and show pages. TV shows often have no Rotten Tomatoes score in OMDb, so you'll see IMDb there.
- **Watching / Paused / Off**: set a show's status on its page. Paused shows (started, but not what you're watching now) move to a **Paused** section on the Watching tab with a **Resume** button, and go to the bottom of Kodi's Where was I?. Watching a new episode of a paused show resumes it automatically.
- **Shows**: filter by Watching / Paused / Off. Turning a show off removes it from Watching and New on the phone *and* from Where was I? in Kodi.
- **Search** (top of Discover): finds TV shows, movies, documentaries and people at once. A "Series: Title" search such as "Frontline: The Tank Man" also tries each part. **Add** follows a show or puts a movie on your list. Use this for Netflix, Prime and so on, then tick episodes yourself. On a show's page, tap an episode for **Mark watched up to here** or **Mark all of season**.
- The show page also says where the show streams in your region (Netflix, Prime, …).
- **Play on TV (▶)** starts the episode on a Kodi box, on whichever TV you choose if more than one is on. The box plays it from Premiumize if the show's files are there, otherwise through Seren. It works from anywhere because it goes through your Google Drive, and takes about 5–10 seconds.
  - Kodi has to be running on the TV, either open or in the background; it comes to the front when the command arrives. If Kodi has been fully closed, the phone says no TV is responding.
  - To tell several TVs apart, set a name for each in TrackMyShows **Settings → General → Name of this TV**.

### Phone setup (once)
1. **Google: add a Web client** to the same Google Cloud project as the Kodi client. It must be the same project, or the phone can't see the Kodi files.
   - **Google Auth Platform → Clients → Create client → Web application**, name it `Phone`.
   - **Authorized JavaScript origins**: add `https://<your-github-username>.github.io`. Add `http://localhost:8000` too if you want to test on your PC.
   - Redirect URIs: leave empty. Click **Create** and copy the **Client ID**. There's no secret to keep.
2. **Publish to GitHub Pages:**
   - Create a GitHub repository named `TrackMyShows` and push this folder to it. `secrets/` and `dist/` are git-ignored.
   - Free GitHub Pages needs a public repository. That's safe here: no keys are committed. The TMDb key is typed into the app, and the Google client ID isn't a secret.
   - In the repository: **Settings → Pages → Build and deployment → Source: GitHub Actions**. The included workflow publishes `phone/` on every push to `main`.
3. **On your phone**, open `https://<your-github-username>.github.io/TrackMyShows/` in Chrome. Use **⋮ → Install app**, then open the app from your home screen.
4. **In the app's Settings**, paste the Google client ID, tap **Save**, then **Sign in with Google**, using the same account as the Kodi boxes. Your TMDb and OMDb keys sync between your devices through a private settings file in your Drive, so you only enter them on the first device.

Google gives the phone a sign-in that lasts one hour. After that, the sync button changes to **Tap to sync**. One tap renews it, usually without asking you anything. Everything else, including marking episodes, works without signing in, and syncs on your next tap.

Test on your PC: `python tools/serve_phone.py`, then open http://localhost:8000. Use this instead of `python -m http.server`: on Windows that server sends JavaScript files with the wrong type and the app won't start.

## Development
```
npm test                              # phone logic (node) + Kodi core (python) tests
python build.py                       # build the Kodi zip only
python tools/make_icons.py            # regenerate icons
```
Debug log: enable **Settings → Advanced → Verbose logging**, then check `kodi.log` for lines tagged `[plugin.video.trackmyshows]`.

## Limitations
- Seren's own watched ticks and Next Up still come from Trakt. Use **Where was I?** instead.
- Anime absolute numbering (`Show - 105.mkv`) and odd file names need a one-time fix in **Needs attention**.
