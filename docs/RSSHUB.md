# RSSHub: what it can actually fetch

Research basis: a fresh clone of `DIYgod/RSSHub` master (`lib/routes/`) plus the live docs site. All figures below are measured from that clone, not from marketing copy.

```text
route namespaces (platforms)     1,983
exported route definitions       3,948
route source files               6,787
license                          AGPL-3.0
```

One "namespace" is one platform/site (for example `bilibili`, `weibo`, `zhihu`, `nytimes`). One platform can expose dozens of routes (`/bilibili/user/video/:uid`, `/bilibili/vsearch/...`, `/bilibili/ranking/...`).

---

## How it works

RSSHub is a Node.js service that exposes HTTP paths which return RSS/Atom/JSON. You request a route; it scrapes or calls an upstream API and returns a feed.

```text
https://rsshub.app/bilibili/user/video/2267573
        ^ instance      ^ route              ^ parameters
```

Three ways to use it:

1. **Public instance** `https://rsshub.app` — zero setup, but shared rate limits, frequent breakage, and routes needing cookies are unavailable.
2. **Self-hosted** (Docker / Node) — required for any route that needs credentials, and strongly recommended for Bilibili, Weibo, X, Instagram, Telegram media, etc.
3. **As an optional adapter** inside your own pipeline — treat each RSSHub route as just another feed URL with health checks.

Ecosystem tools worth knowing:

- **RSSHub Radar** — browser extension that detects the RSS/RSSHub route for the page you are on. This is the fastest way to find the route for a given site.
- **Folo**, **RSSBud** (iOS), **RSSAid** (Android) — readers built around RSSHub.
- **awesome-rsshub-routes** — curated, live-verified route collections.

---

## Coverage by category

Measured route counts per category (routes can appear in several categories):

| Category | Routes | Meaning |
|---|---|---|
| `university` | 654 | Chinese/global university notices, admissions, exam boards |
| `new-media` | 600 | Blogs, newsletters, WeChat mirrors, independent media, personal sites |
| `social-media` | 238 | Bilibili, Weibo, Telegram, X, Instagram, Threads, TikTok, Mastodon, Zhihu, Xiaohongshu… |
| `traditional-media` | 220 | Newspapers, wire services, broadcasters |
| `programming` | 254 | GitHub, GitLab, release notes, engineering blogs, package registries |
| `multimedia` | 203 | Video, audio, podcasts, music, streaming |
| `finance` | 180 | Exchanges, regulators, research houses, market data |
| `game` | 188 | Game news, esports, patch notes, storefronts |
| `anime` | 137 | Anime/manga/comic trackers and studios |
| `bbs` | 136 | Forums and discussion boards |
| `blog` | 146 | Personal and corporate blogs |
| `study` | 89 | Study/exam resources |
| `reading` | 78 | Books, literature, publishing |
| `picture` | 87 | Photo/art communities |
| `journal` | 78 | Academic journals |
| `shopping` | 96 | E-commerce and deal trackers |
| `live` / `sport` / `forecast` / `travel` / `design` | 18–65 each | Specialized verticals |
| `other` | 200 | Everything else |

---

## Platforms relevant to your topics

### Chinese social and content platforms

| Platform | Namespace | Notes |
|---|---|---|
| Bilibili | `bilibili` | ~50 routes: UP submissions, dynamics, collections, favourites, bangumi, manga, live, hot search, search, comments, danmaku, watch-later, messages |
| Weibo | `weibo` | `/user/:uid`, `/keyword/:keyword`, `/search/hot`, `/timeline/:uid`, `/group/:gid`, `/super_index/:id`, `/user_bookmarks/:uid` |
| Zhihu | `zhihu` | daily, hot list, questions, answers, columns, pins, topics, user activity, collections |
| Xiaohongshu | `xiaohongshu` | user notes, boards (needs cookie) |
| 抖音 / Douyin | `douyin`, `newrank/douyin` | live and account routes; anti-crawler, may need Puppeteer |
| 快手 | `kuaishou` | user feed |
| 小红书, 即刻, 豆瓣, 虎扑, NGA, 贴吧-like boards | `jike`, `douban`, `hupu`, `nga`, `t66y`, `discuss`, `saraba1st` | community sources |
| WeChat | `wechat`, `newrank`, `freewechat` | **indirect only** — see `docs/WECHAT.md` |
| 小宇宙 (podcasts) | `xiaoyuzhou` | `/podcast/:id`, `/pickup`, `/comments/:id` |
| 喜马拉雅 / 荔枝 / 蜻蜓 / 猫耳 | `ximalaya`, `lizhi`, `qingting`, `missevan`, `ajmide`, `tingtingfm`, `radio` | Chinese podcast/audio platforms |
| 知识星球 / 小专栏 / 竹白 / 语雀 / 少数派 | `zsxq`, `xiaozhuanlan`, `zhubai`, `yuque`, `sspai` | paid/newsletter-style Chinese publishers |

### English tech and engineering

- `github`, `gitlab`, `gitee`, `bitbucket`, `hackernews`, `lobste.rs`-adjacent boards, `dev.to`, `hashnode`, `medium`, `stackoverflow`-adjacent, `producthunt`, `bestofjs`, `npm`, `pypi`-adjacent registries, `dockerhub`, `maven`, `arxiv`, `openai`, `anthropic`, `deepmind`, `huggingface`, `qwen`, `deepseek`, `ollama`, `cursor`, `zed`, `jetbrains`, `visualstudio`, `chromium`, `nodejs`, `python`, `rustcc`, `lwn`, `phoronix`, `css-tricks`, `smashingmagazine`, `web.dev`, `logrocket`, `netflix tech`-style corporate engineering blogs.
- Individual thought leaders and analysts: `paulgraham`, `stratechery`, `ben-evans`, `simonwillison`-style personal feeds, `gwern`, `danluu`-style blogs, `importai`-style newsletters, `thegradient`, `transformer-circuits`, `towardsdatascience`.

### International media and society

- `nytimes`, `wsj`, `ft`, `ftchinese`, `bloomberg`, `reuters`, `theguardian`, `washingtonpost`, `theatlantic`, `newyorker`, `economist`, `bbc`, `cnn`-adjacent, `npr`, `aljazeera`, `dw`, `lemonde`, `elpais`, `nikkei`, `asahi`, `yomiuri`, `sankei`, `scmp`, `zaobao`, `cna`, `focustaiwan`, `udn`, `mingpao`, `hk01`, `hkej`, `theinitium`, `twreporter`, `thepaper`, `caixin`, `infzm`, `jiemian`, `yicai`, `cls`, `21caijing`, `bjnews`, `xinhua`/`news`, `cctv`, `people`, `guancha`, `huanqiu`, `cankaoxiaoxi`, `qstheory`, `sixthtone`-style outlets.
- Think tanks and policy: `carnegieendowment`, `brookings`, `cfr`, `foreignaffairs`, `nber`, `imf`, `wto`, `un`, `who`, `worldhappiness`, `ccg`, `cdi`, `nifd`, `chinathinktanks`, `pakistan/india`-region outlets.
- Fact-checking and OSINT: `chinafactcheck`, `tfc-taiwan`, `maldita`, `disinfo`, `piyao`, `mygopen`.
- Uncensored/independent Chinese voices: `wainao`, `ngocn2`, `pincong`, `matters`, `theinitium`, `cdt`-style mirrors where present.

### Film, TV, music, culture

- Film/TV: `douban` (movie lists, weekly best, now-showing, "想看"), `imdb`, `letterboxd`, `rottentomatoes`, `metacritic`, `themoviedb`, `trakt`, `deadline`, `vulture`, `maoyan`, `dytt`, `pianyuan`, `zimuku`/`subhd`/`zimuxia` (subtitles), `iqiyi`, `youku`, `qq`/`tencent` video, `netflix`, `iqiyi`.
- Music/audio: `spotify`, `bandcamp`, `soundcloud`, `mixcloud`, `last.fm`, `melon`, `qqmusic`, `castbox`, `soundon`, `transcriptforest`, `podwise`, `xyzrank` (Chinese podcast charts), `storyfm`.
- Games/anime: `steam`, `epicgames`, `nintendo`, `playstation`, `xbox`-adjacent, `gcores`, `gamersky`, `3dmgame`, `indienova`, `yystv`, `bangumi.tv`, `bgmlist`, `manga` trackers, `mihoyo`, `hoyolab`, `kurogames`, `bluearchive`, `fate-go`.

### Finance, society, institutions

`xueqiu`, `wallstreetcn`, `yicai`, `gelonghui`, `jin10`, `futunn`, `laohu8`, `10jqka`, `eastmoney`, `sse`, `szse`, `bse`, `cffex`, `cfmmc`, `sec`-adjacent, `coindesk`, `cointelegraph`, `theblock`, `odaily`, `panewslab`, `wublockchain`-style crypto outlets, `pbc`/`nbs`-style official statistics, government ministries and provincial sites, museums (`chnmuseum`, `dpm`, `shanghaimuseum`, `njmuseum`, …), libraries, patent offices, standards bodies.

---

## What RSSHub cannot do

Be explicit, because this shapes the architecture:

1. **No Reddit.** This master snapshot has **no `reddit` namespace**. Reddit routes were removed. Reddit must be handled by the official Data API (see `docs/SOURCE_MATRIX.md`).
2. **No direct WeChat account scraping.** Only indirect mirrors and third-party feeds.
3. **No authenticated content without your cookies.** Many routes (`weibo/friends`, Bilibili followings, Zhihu timeline, Xiaohongshu, Instagram private, X user timeline, Telegram media) require environment configuration: cookies, tokens, or login sessions. The `requireConfig` flag on each route tells you.
4. **No paywall bypass.** Full-text is limited to what the upstream exposes.
5. **No guarantee of stability.** Routes break when sites redesign. Public instances are rate-limited and blocked more often than self-hosted ones.
6. **Not a historical archive.** Most routes return the current page/list, which may be only the latest 10–20 items. Deep history usually requires an account-backed or own crawler path.

---

## How to use it inside this project

Treat RSSHub as a **feed provider**, not as a crawler framework:

```text
RSSHub route  ->  resolves to an HTTP feed URL
              ->  stored as a normal `feed` source in config/sources.yaml
              ->  our pipeline polls, dedupes, normalizes, reviews
```

Example source entries:

```yaml
- id: rsshub-bilibili-up
  platform: bilibili
  mode: feed
  locator: https://your-rsshub.example/bilibili/user/video/2267573
  topics: [technology]

- id: rsshub-weibo-user
  platform: weibo
  mode: feed
  locator: https://your-rsshub.example/weibo/user/1644114654
  topics: [society]

- id: rsshub-xiaoyuzhou-podcast
  platform: podcast
  mode: feed
  locator: https://your-rsshub.example/xiaoyuzhou/podcast/60822a89e37969823dfb8be3
  topics: [technology, film]
```

Operational rules:

1. **Self-host** if you depend on any cookie-requiring route.
2. **Never poll faster than the route's upstream tolerates.** Daily is plenty for a newsletter; hourly for fast news sources at most.
3. **Health-check every route.** Store HTTP status, content length, and last-successful-item timestamp. Auto-disable after repeated failures.
4. **Prefer native feeds.** If a site has its own RSS/Atom, use it directly instead of a RSSHub route.
5. **Prefer first-party APIs.** For X, Reddit, and Podcast Index, use the official interface; RSSHub is the fallback, not the primary.
6. **Find routes with Radar**, not guesswork: install RSSHub Radar and visit the site.
7. **Read the route's `requireConfig` and `antiCrawler` flags** before adding it. `antiCrawler: true` means expect breakage and lower your frequency.

---

## Route-finding cheat sheet

| You want | Route shape |
|---|---|
| Bilibili UP's videos | `/bilibili/user/video/:uid` |
| Bilibili UP's dynamics | `/bilibili/user/dynamic/:uid` |
| Bilibili search | `/bilibili/vsearch/:kw/:order?/:disableEmbed?/:tid?` |
| Bilibili hot search | `/bilibili/hot-search` |
| Bilibili video comments | `/bilibili/video/reply/:bvid` |
| Weibo user | `/weibo/user/:uid` |
| Weibo keyword | `/weibo/keyword/:keyword` |
| Weibo hot search | `/weibo/search/hot` |
| Zhihu hot list | `/zhihu/hot` |
| Zhihu daily | `/zhihu/daily` |
| Zhihu question | `/zhihu/question/:questionId` |
| Telegram channel | `/telegram/channel/:username` |
| X user | `/twitter/user/:id` |
| X list | `/twitter/list/:id/:name` |
| X keyword | `/twitter/keyword/:keyword` |
| YouTube channel | `/youtube/user/:username` |
| Xiaoyuzhou podcast | `/xiaoyuzhou/podcast/:id` |
| Douban movie list | `/douban/list/:type` |
| Douban now showing | `/douban/movie/playing` |
| WeChat via Wechat2RSS | `/wechat/wechat2rss/:id` |
| WeChat homepage template | `/wechat/mp/homepage/:biz/:hid/:cid?` |
| NewRank WeChat | `/newrank/wechat/:wxid` |

Route parameters and deployment configs are documented at <https://docs.rsshub.app/routes/social-media> and the sibling category pages.

---

## Sources reviewed

- `DIYgod/RSSHub` master clone: `lib/routes/` (1,983 namespaces), `lib/utils/wechat-mp.ts`, `lib/routes/telegram/`, `lib/routes/wechat/`, `lib/routes/newrank/`, `lib/routes/weibo/`, `lib/routes/bilibili/`, `lib/routes/xiaoyuzhou/`
- RSSHub documentation: <https://docs.rsshub.app/>
- RSSHub routes reference: <https://rsshub.netlify.app/routes/social-media>
- Repository: <https://github.com/DIYgod/RSSHub>
- Radar: <https://github.com/DIYgod/RSSHub-Radar>
- Curated routes: <https://github.com/JackyST0/awesome-rsshub-routes>
