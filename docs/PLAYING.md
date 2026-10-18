# Playing on an OpenCS server

How to connect the original DOS client to an OpenCS server and what to do once you are in. The server's
side is in `operations.md` and `DEPLOYMENT.md`.

## What you need

- **Your own copy of the game.** OpenCS does not include it and this project never distributes it: no
  program, art, map or manual. Install it the way its installer says.
- **DOSBox-X or DOSBox Staging**, or any DOS setup with an emulated modem that can dial out over the
  internet. Both emulators' modems take a phonebook file that turns the number the game dials into a
  server address. (The original DOSBox has no phonebook.)
- **The address of a server**: a host name or IP address and a TCP port (the default is 2399). If you run
  the server yourself, see `DEPLOYMENT.md`.

## dosbox-x: the serial port as a modem bridged to TCP

The game thinks it is dialing a modem on a serial port. dosbox-x emulates that modem and, when the game
"dials" a number, opens a TCP connection to whatever address the phonebook maps the number to.

Two things matter beyond the address:

- **8 data bits, no parity, 1 stop bit (8N1).** The in-game protocol is binary and its checksum bytes
  have the top bit set. On a 7-bit link the Chamber (the text lobby) still works, so everything looks
  fine, but every in-game packet is silently damaged and nothing you do has any effect. If the game seems
  to ignore you, check this first (I §3).
- **9600 bps** is what the reference setup uses (`startbps:9600`); the speed is nominal over TCP.

A complete `[serial]` section for `dosbox-x.conf` (or a config file you pass with `-conf`):

```ini
[serial]
phonebookfile = phonebook.txt
serial1=modem startbps:9600 parity:n bytesize:8 stopbits:1
```

The phonebook is a text file next to the config (or give its full path). Each line is a number, a space
and the `host:port` it maps to. Map the number the game dials (next section) to your server:

```
1234 play.example.org:2399
```

Then mount the folder holding the game and start it from `[autoexec]` as you normally would:

```ini
[autoexec]
mount c /path/to/your/game
c:
CS.COM
```

(`CS.COM` is the game's start program; the folder name and the way the game was installed are yours.)

### The game's own settings: account name, password and dial number

The game keeps its settings in a small file, `CS.CFG`, in its folder: **one line of plain text, with the
settings separated by spaces**. Among them, in this order, are the log file name, the **phone number**, the
**account name** and the **password**; a stock install holds the shared sign-up name `JOINGENIE` and a
blank password.

The safe way to change them is the game's own SETTINGS button (offline, before you log on): it rewrites
the file for you. If you edit the file by hand, change only those words and keep the rest of the line,
including the number of fields and the spaces, exactly as they were; a damaged file makes the game show
its first-run setup again. Keep a copy of the original. (If in doubt, use the SETTINGS button.)

Set the dial number to the one in your phonebook line (`1234` above) and the account name and password
to what is described next.

## Logging in

Press LOGON. The game runs its own logon script against the server; you only choose the identity, in
SETTINGS as above.

| account name | what you get |
|---|---|
| `JOINGENIE` (the stock install) | **A guest.** No password is sent. You are given a callsign: the name cut to 8 letters (`JOINGENI`), with its 8th letter replaced by a digit if that callsign is already online (`JOINGEN2`...). Two unmodified clients can therefore play together. Your record is kept under the callsign you were given, which depends on who else is connected, so do not count on it. Guests cannot `/RESUME`. |
| any other name, with a password | **A real account.** The first login with a name registers its password (there is no sign-up step); later logins must give the same one. The callsign is the name, upper-cased and cut to 8 characters. Progress is saved to the account, and `/RESUME` works. Only one login per account at a time. |

Names may be up to 32 characters and passwords up to 64. A wrong password, a name that is already online,
or too many attempts in a short time is refused with a line saying so. Choose a password you use nowhere
else: the server stores only a salted hash, but the link is plain text.

After the script finishes the game shows the online-service menu. Press GO (or F1) and then RETURN at the
prompt to enter the Chamber.

## The Chamber

The Chamber is the lobby. You are in it when the button bar shows PLAY, MORE and QUIT. Type a line and
press RETURN. Commands begin with `/`, in either case:

| you type | the server does |
|---|---|
| `/HELP` | lists the commands |
| `/CITY` | one line per open city: its letter, name and pilots/capacity |
| `/CITY <letter>` | that city's four teams, how many pilots each has, and their callsigns |
| `/PLAY` | joins the busiest city that has room; when all are empty, city B |
| `/PLAY <letter> [team]` | joins that city. `team` is `RED`, `BLUE`, `GREEN`, `BLACK`, or `R`, `B`, `G`, or `0` to `3` (`B` is BLUE; use `BLACK` or `3` for black). If the team is full, or you name none, you are put on the smallest open team and told so |
| `/WHO` or `/STATUS` | every pilot online and where (`CHAMBER`, or city letter and team) |
| `/WHO <callsign>` | that pilot's location and saved record |
| `/SCORE` | your own saved record: last game's score, career total, bay points, bays, battle marker |
| `/RESUME` | return to a game a dropped line interrupted (below) |
| `/COLORS` | shows the colors the server uses for text (a test aid) |
| `/QUIT` | leave the Chamber for the online-service prompt; type `BYE` there to hang up |
| anything else | sent as chat to the other pilots in the Chamber (not to anyone in a game), as `CALLSIGN: text` |

The button bar types for you: PLAY sends `/PLAY`; MORE shows a second row (STATUS, WHO, CITY, SCORE and
the city and callsign variants that type `/CITY ` or `/PLAY ` onto your line for you to finish with a
letter). Typing a single city letter shortly after `/CITY` gets a reminder of how to ask for information
or to play. A command the server does not know is answered with an "unknown command" line.

(`/GO` is not a command you type: the game sends it itself while launching, to say it found the city's
map file.)

### The three cities

Cities are set by the operator (`config/cities/`); the shipped ones are:

| letter | name | map | multiplier ceiling | towers | modules in stock | hazards |
|---|---|---|---|---|---|---|
| A | CYBERCITY ONE | 1 | 8 | Normal output | up to level 2 | none: a place to learn |
| B | CYBERCITY TWO | 2 | 20 | Normal output | up to level 4 | 2 Sentinels; rain that comes and goes |
| C | CYBERCITY THREE | 3 | 40 | Low output | up to level 5 | 4 Sentinels, a free Muncher every 10 minutes; dusk and storms |

Each city's territory is worth different amounts per block (A: a plain middle, B: rings rising to the
center, C: a high-value core), so where you fight matters. Each city holds 16 pilots, 4 per team.
**Teams** are RED, BLUE, GREEN and BLACK; the team decides your home corner. B is the default city.

## In a game

You enter with `/PLAY`; the game shows its loading screen and then drops you into the city. Play is
the game's own. What the server does for you:

- **Orders arrive by guppy.** When you buy a tower or a module, your team's guppy flies the delivery
  from your corner; it lands after its flight, not at once. Towers then belong to your team and earn
  territory.
- **Towers, modules and effects.** Modules are limited by the city's stock level; the server carries out
  the modules' effects. A dropped module can be picked up.
- **Bounty and payouts.** The server keeps every pod's bounty and pays it to those who hit and kill
  you. A death resets your power, multiplier and modules; your battle marker drops by one.
- **Bays persist.** The bays you have upgraded and your battle marker are saved with your account (or
  guest callsign) and restored next time you enter a city.
- **Latency meter.** The server answers the game's ping at once; the meter shows your round trip.

To leave, press **Shift-Q**: the game saves your progress, closes and returns you to the Chamber on the
same connection. After a few seconds the Chamber buttons come back and you can `/PLAY` again without
logging in. An administrator may also send you back, with a message, or announce a restart.

### If your line drops

A real account that loses its connection (crash, closed window, lost link) has the game **held**:

1. Reconnect and log in within **10 minutes** of the drop.
2. In the Chamber the server tells you the game was interrupted and how long you have. You have **3
   minutes after re-entering the Chamber** to type `/RESUME`.
3. `/RESUME` launches you back into the same city and team. Restored: bays and battle marker, score,
   multiplier, bay points and installed modules (as of your last automatic status save, about a
   minute). Not restored: power and armor (you start at full), module charges, bounty and position
   (you drop at your team's corner).

`/RESUME` is **refused for guests**, when nothing is held, and **after a drop in the middle of a fight**:
if your pod was under attack in the 20 seconds before the line dropped, the drop counts as a death, with
payouts to the pilots who hit you, and cannot be resumed. Typing `/PLAY` gives the held game up. A server
restart loses every held game.

## Troubleshooting

| symptom | cause and fix |
|---|---|
| The Chamber works but, in a game, nothing you do has any effect, or the screen shows garbage | The link is not 8-bit clean. The game's packets need 8 data bits, no parity and 1 stop bit (I §3): if your emulator's config sets the serial port's format, set it to that, and make sure nothing between you and the server (a proxy, a telnet-mode bridge) alters bytes |
| After `/PLAY` a line says there is no map and you stay in the Chamber (the game answered `/GO 1`) | The game could not find the map file the city uses. Only maps 1 to 3 ship with the game; a city on another map needs the file from the operator, placed in the game's folder |
| A `/GO 2` or `/GO 3` line | The map does not match the game, or the PC has too little memory free for the game: free conventional or expanded memory (the game needs about 580 KB and expanded memory) and try again |
| The pod is frozen right after loading, or other pilots do not move | The server may be busy or the link slow; wait a few seconds. If it persists, press Shift-Q, or reconnect. Check the link as in the first row |
| The latency meter stays blank | The ping is not being answered, which the server does in every game, so the in-game link is broken: see the first row |
| Nothing after dialing, or "NO CARRIER" | The number you dialed is not in the phonebook, the `host:port` is wrong or unreachable, or a firewall blocks the port. Test from the same machine with `nc host port` |
| The server says `THAT ACCOUNT IS ALREADY LOGGED IN` | The account has a live connection (perhaps your old dropped one, until the server notices it is gone, which can take about 15 seconds); wait and retry |
| `TOO MANY LOGIN ATTEMPTS` or `TOO MANY NEW ACCOUNTS` | The server limits how fast logins and sign-ups can come from one address; wait a few minutes |
| `/RESUME` says nothing is held | You are a guest, the 10 or 3 minutes passed, you used `/PLAY` in between, the drop counted as a death, or the server restarted |
| The server is full: the connection closes at once | A limit of 8 connections per address and 64 in all applies; tell the operator if you share an address with others |

The text of the game and its manual are not repeated here; the manual that came with your copy is the
reference for flying, weapons and modules.
