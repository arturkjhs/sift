# tgclient

Рабочее название. Кроссплатформенный (macOS + Linux) клиент Telegram на TDLib + PySide6
с LLM-функциями (саммари, транскрипция, смысловой поиск) через OpenRouter.
Аудитория: автор и его друзья (IT-комьюнити), потом, возможно, публичный релиз.

## Как работать с этим репо (для Claude Code)

- С автором общаться по-русски, на «ты», прямо и по существу, без социальных смягчений.
- Код, комментарии, docstring'и, коммиты — на английском.
- Окружение — только uv: `uv sync --extra semantic`, запуск через `uv run …`, `uv.lock` в git.
  Не создавать venv руками и не ставить пакеты через pip.
- Тесты: `uv run python -m unittest discover -s tests`. Гонять после любых изменений в `td/`,
  `store/`, `models/`, `services/`, QML.
- Новую логику покрывать тестами на `FakeLib` (`tests/fakes.py`): скриптованный TDLib, без сети.
- Qt-модели проверять через `QAbstractItemModelTester` (см. `tests/test_store.py`,
  `tests/test_history.py`).
- QML проверяется offscreen (`QT_QPA_PLATFORM=offscreen`): `tests/test_ui.py` грузит настоящий QML,
  проходит логин на FakeLib и падает на любом QML-warning. После визуальных изменений снимать
  скриншот через `window.grabWindow().save(...)` и смотреть глазами (светлая и тёмная тема:
  `TGC_THEME=light|dark`).
- Обновлять раздел «Этапы» в этом файле, когда этап закрыт или изменился план.

## Стек (решено)

- Python 3.12+ через uv (dev и сборка), PySide6 (Qt 6). UI на **Qt Quick / QML**, модели (`QAbstractListModel`) на Python.
  Причина: лента сообщений с переменной высотой, плавный скролл и анимации проще в QML.
- **TDLib** через `libtdjson` (ctypes), собирается скриптом `scripts/build_tdlib.sh` в `vendor/tdlib`.
- asyncio; интеграция с Qt через `qasync` (с M1).
- SQLite (FTS5 + эмбеддинги) — только собственный индекс поиска и LLM-данные.
- `httpx` для OpenRouter и транскрипции (с M4).

## Архитектура

```
src/tgclient/
  td/        TDLib: ctypes-биндинг (tdjson.py), async-клиент (client.py), авторизация (auth.py)
  store/     состояние в памяти, обновляемое из TDLib-апдейтов (chats.py, позже users/messages)
  store/history.py   история открытого чата (Qt-free): пейджинг, живые апдейты, ответы
  store/richtext.py  formattedText → HTML для Qt rich text (UTF-16 offsets!)
  store/files.py     FileManager: состояние всех файлов TDLib, загрузки, прогресс
  store/media.py     медиа из content сообщения (Qt-free): фото, видео, стикеры, файлы, голос
  store/markdown.py  markdown саммари → HTML для Qt rich text (цвета темы, компактные списки)
  store/presence.py  онлайн-статусы, «печатает…» (chat actions), число участников + тексты
  store/reactions.py реакции сообщения ↔ ключи для QML (эмодзи, "custom:<id>", "paid")
  store/notifications.py  Notifier: TDLib notification groups → Notice → NotificationSink
  store/album.py     раскладка альбома сеткой (ряды, общая высота в ряду)
  store/custom_emoji.py  кастомные эмодзи: id → стикер (пакетный getCustomEmojiStickers) → файл
  store/link_preview.py  linkPreview → карточка (сайт, заголовок, описание, картинка)
  store/pinned.py    закреплённые сообщения открытого чата/темы для полосы под шапкой
  store/forums.py    форумы: супергруппы с темами, список тем и их счётчики
  store/polls.py     опросы, викторины, чек-листы → вид для QML
  store/keyboards.py клавиатуры ботов: inline-кнопки под сообщением, reply-клавиатура
  models/    Qt-модели для QML: chat_list.py (сортированный список с move-анимацией), folders.py,
             messages.py (лента открытого чата, отправка, прочтение, действия над сообщениями,
             статус в шапке), composer.py (черновики, вложения, вставка из буфера, свой typing),
             chat_picker.py (выбор чата для пересылки), viewer.py (полноэкранный просмотрщик),
             search.py (результаты поиска: секции, debounce, сниппеты), topics.py (темы форума),
             profile.py (панель профиля чата/человека), contacts.py (контакты), gifs.py (GIF)
  ui/        QObject-контроллеры (auth_controller, shell, voice_player, ai_controller,
             lock (пароль и блокировка), chat_actions (меню чата), devices (сессии),
             folders (редактор папок), group_admin (группы/каналы: создание, админка),
             video_note (запись кружков),
             notifications: системные уведомления + бейдж, accounts: мультиаккаунт,
             recorder: запись голосовых, updates: автообновление), animation.py (TGS/WebM:
             кадры + QML-тип `TgClient.Native/AnimatedImage`), image providers
             `image://tg/<account>/...` (images.py), `image://qr/<текст>` (qr.py) и
             `image://icon/<name>/<rrggbb>` (icons.py, свои SVG),
             qml/TgClient/ — QML-модуль (qmldir, Theme-синглтон, вьюхи)
  services/  без зависимости от UI — потом API для плагинов:
             openrouter.py (httpx, ZDR), ai_store.py (SQLite: AI по чату, транскрипты, саммари),
             summary.py (сбор истории, промпты чата и человека, ссылки [m<id>]),
             assist.py (промпты и парсеры M8: перевод, вопросы, события/.ics, ответы,
             дайджест/обещания между чатами, подсказка ответа, релевантность, документы),
             ai.py (AiService: правила доступа), search_index.py (SQLite: FTS5 + векторы),
             embeddings.py (fastembed, локально), search.py (SearchService: индексация, поиск),
             updates.py (GitHub Releases: проверка, загрузка, замена AppImage)
  prefs.py   настройки из UI (тема, уведомления, модели, лимит, язык перевода):
             `data_dir/prefs.json`
  devbundle.py  macOS `uv run`: перезапуск из своего dev-.app (иконка, bundle id)
  accounts.py   реестр аккаунтов `data_dir/accounts.json` (Qt-free)
  vault.py      ключи шифрования: база TDLib (keyring/файл), пароль (scrypt + AES-GCM),
                запечатанные AI-данные (`SealedFile`)
  app.py     GUI: Session (TDLib-клиент + сторы + модели), QML engine, qasync loop
  cli.py     консольная обвязка для отладки слоя td/
```

### Инварианты

- Вся работа с TDLib только через `TdClient.send()` / `TdClient.on()`. Прямых вызовов libtdjson вне `td/` нет.
- `td_receive` вызывается только из потока `TdHub` (один на процесс, раздаёт события по `@client_id`;
  это заранее заложено под мультиаккаунт).
- Обработчики апдейтов выполняются в asyncio loop. Никакого блокирующего I/O в них.
- TDLib — источник истины для сообщений, чатов, файлов. В свою БД сообщения не дублируем.
- `AuthFlow` не знает про UI: UI реализует протокол `AuthUI` (консоль в cli.py, QML через
  `AuthController`).
- `store/` не зависит от Qt. Модели подписываются через `ChatStore.subscribe()`.
- Принадлежность чата к списку = наличие позиции с ненулевым `order` в этом списке. Позиции
  приходят в `updateNewChat`, `updateChatPosition`, `updateChatLastMessage`, `updateChatDraftMessage`
  — все четыре обязательно обрабатывать, иначе порядок чатов разъедется.
- Счётчик упоминаний (@ в списке) меняют два апдейта: `updateChatUnreadMentionCount` и
  `updateMessageMentionRead` (приходит, когда прочитано само сообщение с упоминанием) — нужны оба.
- В TDLib JSON int64 (например, `chatPosition.order`, опция `my_id`) приходят строками, int53
  (id чатов) — числами. В QML id чатов держать как `var`, не `int` (выходят за int32).
- Объекты, отданные в QML как context property, держать живыми со стороны Python (`create_engine`
  кладёт ссылки в `engine._tgclient_refs`), иначе GC удалит их и в QML будет `null`.
- Закрытие окна не останавливает Qt loop сразу: QML вызывает `shell.requestQuit()`, Python
  закрывает TDLib, потом выходит (`setQuitOnLastWindowClosed(False)`).
- Лента: строка 0 = самое новое сообщение, ListView `BottomToTop`. Старые страницы дописываются
  в конец модели (визуально сверху) и не сдвигают то, что видно; новые прилипают к низу.
  Следующая страница истории запрашивается из `data()` для последних 10 строк (как ленивые аватарки).
- Окно истории может не доходить до новейшего сообщения (`ChatHistory.reached_end` = False,
  `messages.atLatest`): `load_around(id)` (`getChatHistory` с `offset=-25`) заменяет окно через
  `history_reset` (модель — `beginResetModel`). Конец окна — по `chat.last_message`
  (`last_message_id`), без него — по короткой странице. Новее грузит `data()` для первых 10
  строк (`load_newer`); живые сообщения за окном не вставляются (был бы разрыв);
  «вниз» и любая отправка (`_clear_unread_separator`) → `load_latest()`. `stickToBottom`
  действует только при `atLatest`, иначе низ окна тянул бы все страницы подряд. `jumpTo`:
  до `NEAR_JUMP_PAGES` = 3 страниц вверх догружает, дальше — `load_around`.
- `ChatHistory` меняет список только через `HistoryListener` с `commit()`: модель оборачивает его в
  `begin/endInsertRows` и т.п. Колбэки идут через `_Adapter`, который игнорирует историю
  предыдущего открытого чата, если её страница пришла после переключения.
- После вставки/удаления сообщения пересчитываются соседи (группировка, метки дня зависят от них).
- `getChatHistory` с `from_message_id=0` часто отдаёт 1 сообщение из локального кэша — поэтому
  `load_initial` повторяет запрос, пока страница не наполнится или история не кончится.
- Entity offsets в TDLib — в UTF-16 code units, в Python — code points. Только через
  `utf16_index_map`.
- Время в пузыре: в конец HTML добавляется невидимая копия метки времени, поэтому последняя
  строка текста оставляет под неё место, а видимая метка рисуется поверх в правом нижнем углу.
- ListView ленты: `acceptedButtons: Qt.NoButton` (Qt 6.9+), чтобы перетаскивание мышью выделяло
  текст, а не таскало список. Колесо и тачпад работают.
- Файлы: `FileManager` — единственный источник состояния. Объекты файлов внутри сообщений и
  чатов — устаревающие снимки, поэтому они только `register()` (если файл неизвестен), а
  `updateFile` и ответ `downloadFile` делают `update()`. Не обходить это. Ответ `downloadFile`
  применяется, только если с момента запроса не было `updateFile` по этому файлу: TDLib
  присылает «completed» раньше, чем корутина получит устаревший ответ «downloading» (из-за этого
  не показывались аватарки). Фейки в тестах должны воспроизводить этот порядок.
- Аватарки отправителей: `UserStore` хранит `profile_photo.small`, роль `senderAvatar` качает
  лениво (как `avatarSource` в списке чатов), обновление — через `MEDIA_ROLES`.
- Картинки в QML только через `image://tg/<account>/<kind>/<file_id>[/<radius_px>]`, URL строит
  только `FileManager.url()` (у каждого TDLib свои id файлов, а QML кэширует по URL): avatar
  (круг), media (cover-crop + скругление, радиус в физических пикселях), mini (minithumbnail
  TDLib как размытый плейсхолдер), sticker (fit, прозрачность; у TGS/WebM — первый кадр),
  full (файл как есть, просмотрщик). Скругление — в Python (QPainter), а не шейдерами: работает
  в software-рендере и offscreen-тестах.
- Превью (фото, статические стикеры, миниатюры видео/документов) качаются автоматически с
  приоритетом 1, когда строка попадает в `data()`; так же — файлы анимированных стикеров и
  GIF до 10 МБ (роль `playbackPath`). Остальное — по клику (приоритет 32). Фото, видео, GIF и
  кружки по клику открываются во встроенном `MediaViewer` (`viewerRequested` → `ViewerModel`:
  снимок фото/видео загруженной истории, ←/→, качает показанный файл).
- Альбомы: строки истории остаются по одной на сообщение. Самое новое сообщение альбома
  (наименьший row) рисует сетку (`mediaKind: "album"`, `albumItems`, подпись любого участника),
  остальные — `albumHidden` (высота 0). Соседи для группировки/меток дня у альбома берутся за
  его концом (`_older_row`); вставка/удаление участника обновляет весь альбом
  (`_refresh_albums`). Альбом из одного загруженного сообщения — обычное сообщение.
- Анимации: TGS — rlottie, WebM-стикеры — PyAV с декодером libvpx-vp9 (родной VP9 FFmpeg, и
  значит Qt, теряет альфу). Кадры рендерятся один раз на (файл, размер) в пуле потоков и
  лежат в общем LRU-кэше (160 МБ), поэтому повтор цикла не стоит CPU. `AnimatedImage` —
  единственный QML-тип, регистрируемый из Python (`register_qml_types()` в `create_engine`);
  играет, только пока виден и приложение активно. Делегаты ленты уничтожаются в любой момент (прокрутка,
  смена чата), поэтому колбэки из фоновых задач держат такие элементы только через
  `weakref` + `shiboken6.isValid()`, а `FrameCache` изолирует ошибку одного получателя. GIF (MP4) — `MediaPlayer` + `VideoOutput`
  без звука, через `Loader` только на экране.
- Кастомные эмодзи: `formatted_to_html(..., custom_emoji=)` заменяет текст сущности на `<img>`,
  когда картинка скачана, иначе остаётся запасной эмодзи; модель запоминает, какие сообщения
  ждут какой id, и перерисовывает их по событию `CustomEmojiStore`. Анимированные — первым кадром.
- Эмодзи и стикеры: кнопка в `Composer` → `EmojiStickerPicker`. Эмодзи — из `store/emoji.json`
  (генерирует `scripts/gen_emoji.py`, Emoji 15.0 — то, что рендерит macOS 13.4+, без оттенков
  кожи), недавние — `data_dir/recent-emoji.json`; вставка в позицию курсора, попап не закрывается.
  Стикеры — `StickerStore`: наборы и недавние грузятся при первом открытии, решение «какую вкладку
  открыть» — только после события `loaded` (наборы и недавние приходят в любом порядке).
  Отправка: `inputMessageSticker{sticker: inputSticker{sticker, thumbnail, width, height}, emoji}`
  (на нашем коммите TDLib файл обёрнут в `inputSticker`, как и у фото).
- GIF: вкладка «GIFs» в `EmojiStickerPicker` (`GifModel` = `gifs`): сохранённые
  (`getSavedAnimations`) или поиск инлайн-ботом @gif (`searchPublicChat` → `getInlineQueryResults`,
  через 0.35 с после ввода); превью — статичные миниатюры. Отправка —
  `messages.sendAnimation` (`inputMessageAnimation` с `inputFileId`, без загрузки) +
  `addSavedAnimation`.
- Голосовые: один `VoicePlayer` (QtMultimedia, FFmpeg-бэкенд, Ogg/Opus проверен) на всё
  приложение; при старте воспроизведения шлёт `openMessageContent` (отметка «прослушано»).
  Запись — `VoiceRecorder`: PCM 48 кГц mono через `QAudioSource`, кодирование в Ogg/Opus PyAV
  (у FFmpeg Qt нет Opus-энкодера) в потоке, волна — 100 пиков по 5 бит (`encode_waveform`).
  Доступ к микрофону — `QMicrophonePermission` (на macOS нужен `NSMicrophoneUsageDescription`
  в Info.plist — и в .app, и в dev-бандле). Отправляется в чат, где начали запись.
- Кружки: «+» → «Video message» → `VideoNoteRecorder` (`videoRecorder`): `QCamera` →
  `QVideoSink`, кадры ~25 fps обрезаются по центру в квадрат 384 и хранятся JPEG (минута ≈ 30 МБ,
  а не 600 сырыми), звук — PCM как у голосовых; «Send» → PyAV в потоке: MP4 H.264
  (`h264_videotoolbox` → `libx264` → `mpeg4`) + AAC → `inputMessageVideoNote`. Превью —
  `image://camera/<n>` (круг, зеркально; записывается без зеркала), до 60 с. Нужны
  `QCameraPermission` и `NSCameraUsageDescription` (оба Info.plist).
- Мультиаккаунт: `AccountManager` держит `Session` каждого аккаунта запущенной (уведомления и
  бейдж — по всем), QML показывает активный: переключение = `bind_session()` заново ставит
  контекстные свойства. Один `TdHub` и один бэкенд уведомлений на процесс; ключи уведомлений
  `"<account>/<group>:<id>"`, клик маршрутизирует менеджер; бейдж — сумма. Первый аккаунт живёт
  в старом месте (`data_dir/prod`), новые — `data_dir/accounts/<key>/prod`. Неактивный аккаунт
  закрывает открытый чат и выставляет `online=false`. Выход — `logOut` (TDLib удаляет свою БД),
  плюс наши `ai.sqlite3`/`search.sqlite3` (`wipe_local`).
- Вход по QR: `AuthUI.ask_phone()` может вернуть `QR_LOGIN` → `requestQrCodeAuthentication`;
  ссылка `tg://login?token=…` рисуется `image://qr/`. Из этого состояния TDLib не пускает назад к
  номеру, поэтому «Log in by phone number» = `AccountManager.restartLogin()` (logOut + новая
  сессия в той же папке).
- QML-движок разбирать через `dispose_engine()`: сначала окна, потом движок — иначе при
  очистке контекста все биндинги переоцениваются на null (поток TypeError при выходе).
- Обновления: только у сборок с `UPDATE_REPO` (CI вшивает `GITHUB_REPOSITORY`), раз в сутки, один
  анонимный запрос к api.github.com; ассет выбирается по суффиксу имени (`-macos-<arch>.dmg`,
  `-linux-<arch>.AppImage`) — имена артефактов не менять. AppImage заменяет себя и просит
  перезапуск; на macOS .dmg скачивается и открывается; Flatpak — только ссылка.
- Лента прилипает к низу (`stickToBottom`), пока пользователь сам не прокрутил вверх: делегаты
  и картинки догружаются после первого позиционирования и иначе сдвигают низ.
- Непрочитанное: граница — `last_read_inbox_message_id` на момент открытия чата. Модель
  грузит историю вокруг неё (`load_around`, как бы далеко ни было), ставит разделитель над
  первым входящим после границы и шлёт `unreadReady(id)`; лента держит его наверху
  (`anchorId`, `ListView.End` в `BottomToTop`), пока пользователь не прокрутит.
  Разделитель живёт до закрытия чата или до своей отправки. Над лентой — `JumpButton`ы:
  «вниз» с `unreadCount`, «@» (`mentionCount`, `nextMention` — самое старое из
  `searchChatMessages` c `searchMessagesFilterUnreadMention`) и ♡ (`reactionCount`,
  `updateChatUnreadReactionCount`/`updateMessageUnreadReactions`); правый клик — «прочитать
  все» (`readAllChatMentions`/`readAllChatReactions`).
- Черновики: `ComposerModel` — единственный, кто пишет `setChatDraftMessage` (через 1.5 с после
  ввода и при смене чата/выходе). Своё эхо из `updateChatDraftMessage` распознаётся по
  `_synced`; чужой черновик заменяет ввод, только если пользователь не печатал с последней
  синхронизации. Текст при отправке уходит с `clear_draft: true`. Загрузка черновика в поле —
  через `MessageView.loadDraft()` с `draftSuspended`, иначе он сохранился бы обратно и в чат
  ушёл бы «печатает…». В новом TDLib черновик — `draftMessage.content: draftMessageContentText`
  (`draft_text()` понимает и старый `input_message_text`).
- Действия над сообщением: меню открывается только после `actionsReady` — модель спрашивает
  `getMessageProperties` и `getMessageAvailableReactions` (при ошибке — эвристика). Правка
  берёт текст через `getMarkdownText` (форматирование сохраняется) и уходит
  `editMessageText`/`editMessageCaption`. Вложения из диалога/drag-and-drop/буфера сначала
  попадают в `ComposerModel.staged` (окно с подписью) и уходят по одному в одной корутине —
  иначе файл с подписью (ждёт `parseMarkdown`) обгонял бы следующие.
- Уведомления решает TDLib (mute, упоминания в замьюченных, тихие сообщения, снятие после
  прочтения); нужна опция `notification_group_count_max` > 0 (ставит `Notifier.start()`).
  Мы только не показываем открытый чат в активном окне, из апдейта группы берём новейшее и
  режем всплески (5 за 3 с). Бэкенды: macOS — UserNotifications через pyobjc, только в своём
  бандле: bundle id `APP_ID` (.dmg) или `APP_ID.dev`. Чужой бандл не годится: Homebrew-Python —
  framework-сборка внутри `Python.app`, и разрешение/уведомления шли бы от «Python» с его
  иконкой. Поэтому `uv run tgclient` на macOS один раз собирает `<cache>/dev/tgclient.app`
  (копия бинарника Python, наша иконка, ad-hoc подпись) и делает `execve` из него с
  `__PYVENV_LAUNCHER__` = venv-python (`devbundle.py`; выключить — `TGC_NO_DEV_BUNDLE=1`, тогда
  `osascript` без кликов). Linux — D-Bus через jeepney
  (у QtDBus в PySide6 нельзя передать `uint32` в `Notify`); offscreen — `NullBackend`.
  Бейдж — `QGuiApplication.setBadgeNumber` (непрочитанные сообщения без mute в main).
  `setOption online` следует за активностью окна.
- AI (M4): все правила приватности — в `AiService`, не в UI. По умолчанию выключено для каждого
  чата; секретные чаты нельзя включить и они никогда не отправляются; ничего не уходит без
  явного клика. Дайджест и умные уведомления — свои флаги в `AiStore` (`chat_flags`),
  действуют только вместе с «AI on» (`AiService.flag()`); общий «AI on» их не включает. Умные
  уведомления — единственное автоматическое: `Notifier` для таких чатов сначала спрашивает
  `AiService.is_relevant` (дешёвая модель, при ошибке — показать). Каждый запрос в OpenRouter
  идёт с `provider: {zdr: true, data_collection: "deny"}` — без фолбэка на другие провайдеры.
- Premium (`User.is_premium` у меня): голосовые расшифровывает сам Telegram — `recognizeSpeech`,
  результат приходит в `voice_note.speech_recognition_result` (опрос `getMessage` раз в секунду
  до 90 с), без OpenRouter и без «AI on» (звук не уходит дальше Telegram); кнопка Transcribe
  видна при `ai.premium`. Остальные — как раньше, через OpenRouter.
- Все запросы к модели — только через `AiService._complete` (транскрипция — через `_record`):
  проверка месячного лимита на чат (`monthly_limit`, USD; чат 0 — запросы по нескольким чатам),
  кэш одинаковых запросов (`AiStore.cache`, 14 дней, бесплатно), учёт `usage.cost` из ответа
  OpenRouter (`"usage": {"include": true}`) в `AiStore.usage`. Модели: основная (саммари,
  ответы), дешёвая (перевод, релевантность), транскрипция — меняются в Settings (prefs).
- Саммари: модель видит строки `[m<id>] <время> <отправитель>: <текст>` и цитирует `[m<id>]`;
  `linkify` превращает в `tgc://message/<id>?t=<unix-дата>` только id, которые реально были во
  входе. Клик → `messages.jumpTo()` догружает историю вверх до сообщения
  (`ChatHistory.load_until`); `parse_message_link` отбрасывает `?t=`.
- Ссылки на сообщения в AI-тексте — сноски: `linkify` хранит `[](tgc://message/<id>?t=<дата>&s=
  <отправитель>)`, не больше `MAX_CITES` = 2 на утверждение (соседние цитаты `[m1] [m2]`
  считаются вместе; промпты просят «самые показательные первыми»). При показе
  `summary.number_links` нумерует их 1, 2, 3… по порядку чтения (повтор — тот же номер; старые
  саммари с метками времени тоже), `store/markdown.py` рисует номер юникодными надстрочными
  цифрами вплотную к слову (`vertical-align:super` в Qt нечитаемо мелкий, пробел даёт перенос),
  подряд — через `˒`. Подсказка при наведении — `AiController.linkTooltip` →
  `summary.link_tooltip`: «Eugene, 02.10 19:56».
- Метки времени сообщений в AI — одна функция `store/format.message_stamp`, без слов (язык
  саммари любой): сегодня `15:52`, другой день этого года `01.10 15:52`, другой год
  `01.10.25 15:52`, локальное время. Ею пишутся строки входа модели (`message_line`) и подсказки
  у сносок (считаются при наведении, поэтому «сегодня» — день просмотра); каждый промпт со
  строками начинается с `summary.today_header()` («Today: 02.10.2026, Friday» + как читать
  метки). Своих форматтеров времени не заводить.
- Саммари чата (`summary.SYSTEM_PROMPT`) — выводы, не пересказ: без вступлений/заключений;
  первым — `## {H:for_you}` («Касается тебя» на языке саммари), только если есть что; дальше
  ≤7 тем по важности, тема — `**Заголовок.**` + 1–3 предложения, вывод первым, имена — только
  когда автор важен, никаких «кто что сказал», расхождение — одной фразой «A vs B»; шум
  пропускается; 120–250 слов на сутки. Что касается пользователя, размечает код, а не модель
  (`summary.reader_marks`): `(you)` — своё, `↩you` — ответ на моё (автор сообщений вне окна —
  через `getMessages`), `@you` — `textEntityTypeMentionName` с моим id или
  `textEntityTypeMention` с моим `@username` (offsets UTF-16). В шапке — `Reader: Имя
  (@username)`, `Period:` и `For the reader: m12, m15` (`Source.for_you`: только `↩you`/`@you`).
  Раздел «Касается тебя» решает код, модель только пересказывает: `summary.keep_for_you`
  оставляет в нём пункты, цитирующие эти id, и убирает раздел целиком, если их нет (вопрос
  «ко всем» туда не попадает, как бы ни был похож).
- `AiStore` читается целиком в память при старте; запись — через `asyncio.to_thread`.
- Результаты AI («задания») адресуются `(chat_id, subject)`: `""` — саммари чата,
  `"user:<id>"`/`"chat:<id>"` — человек (его сообщения через `searchChatMessages` с
  `sender_id`; разделы «О чём пишет», «В чём разбирается и чем может помочь», «Между
  вами» — без «кто это»/роли/характера и чувствительных черт; во вход добавляются мои
  сообщения из обменов с ним (на которые он ответил и мои ответы ему, `(you)`), «Между вами»
  оставляет код по ним и по ↩you/@you — `keep_for_you`, как «Касается тебя»), `"ask"` — вопрос к чату
  (кандидаты из `SearchService` + последние 60 сообщений), `"events"` — даты и встречи (JSON →
  `data.events` → `.ics` с «плавающим» локальным временем), `"answers:<id>"` — ответы на
  сообщение (сообщения после него до 3 дней, без ботов; таблица `Хто | Позиція | Відповідь`;
  «Без ответа» — только при известных адресатах: упомянутые в вопросе (`summary.mentioned`,
  @username → `searchPublicChat`) или все участники группы до `SMALL_GROUP` = 30; боты
  (`userTypeBot`, `User.is_bot`) и автор вопроса исключаются, без адресатов строку вырезает
  `assist.drop_no_answer`),
  `"doc:<id>"` — вопрос к файлу (PDF уходит целиком как `file` с `file-parser` engine `native`,
  текстовые — текстом). Чат 0: `"digest"` (чаты с флагом, с прошлого дайджеста —
  `kv.digest_since`, не больше 7 дней) и `"promises"` (14 дней AI-чатов, где я писал). Там
  ссылки — короткие `[m1]…` по порядку чтения → `tgc://message/<chat>/<id>`
  (`Source.targets`); клик открывает чат и прыгает к сообщению. Все задания хранятся в
  `results` (с вопросом, стоимостью и `data`).
- Язык AI — одна настройка «Language of AI answers» (`ai_language` в prefs, раньше
  `translate_to`; по умолчанию первый поддерживаемый из языков интерфейса системы, не региона,
  иначе English): на нём всё, что AI пишет пользователю — саммари, объяснения, ответы, дайджест,
  события, переводы. Промпты пишут `{language}`, подставляет `assist._lang()` /
  `summary.language_name()`; «язык чата» в промптах остаётся только для вариантов ответа
  собеседнику. Названия языков в Settings — родные (`assist.NATIVE`).
  Фиксированные слова вывода (заголовки разделов Explain и профиля человека, шапка таблицы
  ответов, «Nothing important», «No answer») не переводит модель: они даются готовыми из
  `assist.L10N` через метки `{H:<key>}`, пояснения к разделам — в скобках с запретом копировать,
  а `clean_headings()` срезает то, что модель всё же дописала к заголовку. Меняя промпты Explain
  и Suggest reply, поднимать `assist.PROMPT_VERSION` (их кэш ключуется сообщением, не текстом).
- qasync, в отличие от `asyncio.run`, не ставит `sys.set_asyncgen_hooks`: недочитанный
  асинхронный генератор (поток httpx) иначе закрывается GC вне цикла — «async generator ignored
  GeneratorExit» / «no running event loop». `main()` ставит хуки цикла
  (`install_asyncgen_hooks`) и при выходе зовёт `shutdown_asyncgens()`; `OpenRouter.stream`
  дочитывает тело после `[DONE]`. Тест — живой SSE-сервер с выключенными хуками.
- Сообщение по правому клику: «Explain» и «Suggest reply» (задания `explain:<id>` / `reply:<id>`
  в панели AI). Контекст собирается при открытии меню (`AiService.context_size`, только TDLib,
  кэш 2 мин) — число сообщений видно в пункте до клика: цепочка `getRepliedMessage` (до 6),
  `getChatHistory` с `offset=-20` (20 до и 20 после), последнее саммари чата; для ответа ещё до
  15 своих сообщений из чата (`searchChatMessages` с `sender_id` = я) как образец стиля.
  Explain — дешёвая модель, фиксированные `##`-разделы (суть, что хотят, контекст со ссылками,
  тон, неясно; пустые опускаются), запрет домысливать мотивы и выводить чувствительные черты.
  Первая строка ответа — `KIND: actionable | informational | light` (`assist.parse_explain`,
  терпит недописанный поток; в `data.kind`). `light` (шутка, эмодзи, согласие) — вместо разбора
  `REACTIONS:` (2–3 из `getMessageAvailableReactions`, которые уходят в промпт; чужие отсекаются,
  недостающие добираются из доступных) и `REPLY:` (короткая реплика в тон), суть — одна строка,
  контекст — только если есть. Панель: `light` — чипы реакций (`messages.addReaction` →
  `addMessageReaction` с `reactionTypeEmoji`) и реплика с «Insert»; `actionable` — кнопка
  «Suggest replies»; `informational` — только текст.
  Suggest reply — основная модель, формат `ANALYSIS:` / `### стратегия` / текст / `LANG:` /
  `TRANSLATION:` (`parse_reply_options` терпит недописанный поток); ответ — на языке чата,
  перевод — на язык из Settings, и только если язык ответа не из «I also read»
  (`read_languages` в prefs, `AiService.reads` = язык AI + они): `LANG:` пишет модель, перевод
  у ответа на читаемом языке убирает код. «Insert» кладёт текст в поле ввода с `replyToId`, не отправляет; «Another
  option» передаёт уже предложенные стратегии. Оба стримятся (`OpenRouter.stream`, SSE) с
  частичным состоянием `pending`; кэш по (чат, сообщение, `edit_date`, действие, язык, модель,
  читаемые языки, модификатор) — повторный клик бесплатен.
- Ссылки Telegram в ленте (`t.me/…`, `telegram.me`, `tg:`) идут в `messages.openLink` →
  `store/links.resolve`: `getInternalLinkType` → публичный чат/бот (`searchPublicChat`),
  номер (`searchUserByPhoneNumber`), инвайт (`checkChatInviteLink`: `chat_id` ≠ 0 — открыть,
  иначе `inviteReady` → окно с «Join», `joinChatByInviteLink`), иначе `getMessageLinkInfo` →
  `linkResolved(chat, message)`; `tg://user?id=` — `createPrivateChat`, `tg://search?q=`
  (хэштеги) — `searchRequested` → поиск в сайдбаре. Остальное — браузер.
- Права на запись: канал — владелец или админ с `can_post_messages`, группа — пока я в ней
  (`PresenceStore.group_status` из `updateSupergroup`/`updateBasicGroup`). Открытый по ссылке
  чат, где я `chatMemberStatusLeft`, показывает «Join» вместо поля ввода (`joinChat`).
- Поиск (M5): индекс `search.sqlite3` хранит только токены (FTS5 `content=''`,
  `contentless_delete=1`) и векторы, без текста сообщений — тексты результатов берутся из TDLib
  (`getMessages`). Секретные чаты не индексируются. Поиск = RRF из трёх списков: локальный FTS5,
  локальные векторы (порог косинуса 0.3), серверный `searchMessages` TDLib. Индексация: живые
  апдейты + медленный backfill истории (150 чатов, до 3000 сообщений / 365 дней на чат, пауза
  0.4 с, FLOOD_WAIT → ждать). Векторы — в памяти одной матрицей float32, в БД float16.
- Поиск по смыслу выключен по умолчанию (модель ~220 МБ качается один раз в `data_dir/models`),
  fastembed — extra `[semantic]`; без него поиск просто ключевой.
- QML-грабли: имя `SearchField` занято в QtQuick.Controls 6.10 — наш компонент `SearchBox`.
  `Text.MarkdownText` игнорирует `linkColor` → саммари рендерятся своим `store/markdown.py`.
  `Text.StyledText` не понимает CSS: цвет через `<font color>`.
- Меню — только `AppMenu`/`AppMenuItem`/`AppMenuSeparator` (свои стили, иконки, тень без шейдеров),
  не голые `Menu`/`MenuItem` Basic-стиля. Кнопки — `PillButton`/`IconButton`.
- В тестах Qt-события идут только через `pump()`: анимации (появление попапов) докручивать
  циклом `settle()`, иначе на скриншоте попап прозрачный.
- Фото скачиваются лениво: роль `avatarSource` при первом запросе вызывает `request_photo()`,
  то есть грузится только то, что видно в списке.

## Шифрование и пароль

- База TDLib каждого аккаунта шифруется своим случайным 32-байтным ключом (`Vault`,
  `data_dir/vault.json`): без пароля ключ в системном keyring (Keychain / Secret Service;
  нет keyring или `TGC_KEY_STORE=file` — в `vault.json`, 0600). Старая база (есть `td.binlog`,
  ключа нет) открывается пустым ключом, после входа — `setDatabaseEncryptionKey` и только
  потом ключ сохраняется (`Session._encrypt_old_database`).
- Пароль (Settings → Passcode, `LockController` = `lock` в QML): ключи хранятся только
  обёрнутыми (scrypt → AES-GCM, `derive(master, account)`) и уходят из keyring; при запуске —
  сначала отдельное окно `LockWindow.qml` (`unlock_at_launch`), сессии создаются после
  разблокировки. С паролем `ai.sqlite3`/`search.sqlite3` живут в памяти (`initial`/`snapshot`)
  и пишутся зашифрованными `*.sealed` раз в 2 мин (если менялись) и при выходе; ключ —
  `derive(db_key, "ai-data")`. Снятие/установка пароля на ходу переключает `SealedFile.key`,
  окончательно файлы переписываются при закрытии. Блокировка (Cmd+L, авто через N минут без
  ввода — фильтр событий на приложении): `LockScreen` поверх окна, TDLib работает,
  уведомления без текста (`Notifier.private`). Выход из аккаунта удаляет его ключ.

## Юридические ограничения

- tdesktop под GPLv3: читать код можно, копировать код, иконки, звуки, `.style`-файлы нельзя
  (если лицензия проекта не GPLv3).
- Не называть приложение «Telegram», не использовать официальный логотип (Telegram API ToS).
- Соблюдать https://core.telegram.org/mtproto/security_guidelines.

## UI (решено в M1)

- QtQuick Controls стиль **Basic** + свой `Theme.qml` (токены цветов и шрифтов). Нативные стили
  Qt не дают кастомизировать контролы. Шрифт системный.
- Палитра: холодные нейтральные + один акцент pine-teal (`#0E7C66` / тёмная `#3FB295`) только для
  того, что требует внимания: непрочитанное, активная папка, основные кнопки. Не копировать синий
  Telegram.
- Тема: system / light / dark (`shell.theme`, env `TGC_THEME`).
- Строки UI на английском через `qsTr()` — под будущую локализацию (RU/UA/CZ).
- Грабли QML: свойство с именем `onXxx` парсится как обработчик сигнала (было `onAccent`).
- Пузыри: входящие белые/графитовые, исходящие с лёгким оттенком акцента; угол у «хвоста»
  группы скруглён меньше (`bottomLeftRadius`/`bottomRightRadius`, Qt 6.7+). Имя отправителя —
  цветом из палитры аватарок. Ссылки без подчёркивания.
- Мультивыбор: Cmd/Ctrl-клик по пузырю (или «Select» в меню) — выбрать, Shift-клик —
  диапазон от последнего, при активном выборе обычный клик переключает; альбом выбирается
  целиком. Вместо поля ввода — панель Copy / Forward / Delete, Esc снимает. Модель: роль
  `selected`, `toggleSelected`/`selectRange`/`deleteSelected`/`forwardSelected`/`copySelected`
  (id по возрастанию — порядок для `forwardMessages`).
- Взаимодействие: двойной клик по пузырю — ответить; правый клик — меню (Reply, Copy text);
  Enter — отправить, Shift+Enter — перенос; клик по цитате — прыжок к сообщению (если загружено).
- Форумы (`is_forum` из `updateSupergroup` → `ForumStore`): чат открывается списком тем
  (`messages.topicsMode`, `ChatHistory.topic_id = TOPIC_LIST` ничего не грузит, `TopicList.qml`
  из `TopicListModel`); `openTopic(id)` — история темы (`getForumTopicHistory`, живые
  сообщения фильтруются по `message.topic_id`), `closeTopic()` — назад. Всё, что уходит в
  чат из открытой темы, несёт `topic_id: messageTopicForum` (`sendMessage`, черновик, typing,
  поиск упоминаний и закрепов); счётчики «вниз»/«@» — темы. `jumpTo` в форуме сначала
  спрашивает сообщение (`getMessage`) и открывает его тему. Unread темы приходит без числа
  (`updateForumTopic`) — `ForumStore` переспрашивает `getForumTopic`. Саммари в теме —
  subject `topic:<id>` (`AiController.topicId` из QML, `summary.collect(topic_id=)`), шапка
  промпта «Чат › Тема».
- Поиск в чате: Cmd/Ctrl+F при открытом чате (Cmd/Ctrl+Shift+F или без чата — глобальный в
  сайдбаре), кнопка-лупа в шапке. `messages.searchInChat` → `searchChatMessages` (с темой),
  по 50, Enter/↑ — старше (догружает `next_from_message_id`), Shift+Enter/↓ — новее; каждый
  шаг — `chatSearchJump(id)` → `showMessage`. Совпадения в тексте подсвечивает
  `richtext.highlight_html` (только вне тегов; метка времени вставляется после — через
  `_TAIL`-заглушку, иначе подсвечивались бы её цифры).
- Комментарии к постам канала: роль `comments` (`interaction_info.reply_info.reply_count`, -1 —
  комментариев нет) → полоса в пузыре; клик — `messages.openComments` → `getMessageThread` →
  модель переключается на группу обсуждения с `ChatHistory(thread_id=)`
  (`getMessageThreadHistory`, живые — по `topic_id.message_thread_id`), всё отправляется с
  `topic_id: messageTopicThread`; «назад» — `closeComments` (открыть канал и прыгнуть к посту).
- Кнопки ботов: роль `inlineKeyboard` → ряды кнопок под пузырём (`messages.pressButton`):
  URL/LoginUrl/User — `openLink`, Callback — `getCallbackQueryAnswer` (ответ — тост или окно
  при `show_alert`, `url` — открыть), CopyText — буфер; игры, WebApp, оплата и т.п. — тост «только
  в официальных приложениях». Reply-клавиатура — `chat.reply_markup_message_id`
  (`updateChatReplyMarkup` несёт сообщение, иначе `getMessage`) → `messages.replyKeyboard` над
  полем ввода, кнопка шлёт свой текст, `one_time` прячется после нажатия, «×» — спрятать.
- Опросы/чек-листы: роль `poll` (`store/polls.poll_view`) → `PollContent.qml` в пузыре.
  Голос — `messages.vote(id, [индексы])` (`setPollAnswer`, `[]` — отозвать; один ответ — сразу
  по клику, несколько — галочки и «Vote»), результаты после голоса/закрытия; `updatePoll`
  приходит без сообщения — `ChatHistory` ищет его по `poll.id`. Пункты чек-листа —
  `markTask` (`markChecklistTasksAsDone`), если `can_mark_tasks_as_done`.
  Создание: «+» в поле ввода → меню (файл / опрос) → `PollEditor.qml` → `messages.sendPoll`
  (`inputMessagePoll`, викторина — `inputPollTypeQuiz` с правильным вариантом и пояснением).
- Профиль (`ProfileModel` = `profile`, `ProfilePanel.qml` справа вместо панели AI — открыта
  одна из двух): клик по заголовку чата или «View profile» в меню человека. Человек —
  `getUserFullInfo` (bio, `bot_info.short_description`), `getGroupsInCommon`; группа —
  `getSupergroupFullInfo`/`getBasicGroupFullInfo`, участники (`searchChatMembers`, владелец и
  админы первыми). Вкладки Media/Files/Links/Voice — `searchChatMessages` с фильтрами, по 50,
  догрузка у конца. Картинки строк ждут загрузки через `_pending` (file id → строки).
- Папки: вкладки — только папки TDLib и «All chats» (`FolderModel`); архив — строка-шапка
  списка «Archived chats» (`chatList.archiveCount/archiveUnread/archivePreview`, число чатов из
  `updateUnreadChatCount.total_count`, при старте грузятся 20 архивных), внутри архива — «←
  Archive». Правый клик по вкладке / кнопка-папка: `FolderEditor` (`folderEditor`) —
  `getChatFolder` → `FolderEditorDialog` (имя ≤ 12, типы чатов, выбранные чаты) →
  `createChatFolder`/`editChatFolder` (иконка, цвет, исключённые чаты сохраняются как были),
  `deleteChatFolder`, «Move left/right» — `reorderChatFolders` с позицией «All chats».
- Группы и каналы (`GroupAdmin` = `groupAdmin`): карандаш в сайдбаре → «New group»
  (`createNewBasicGroupChat` с выбранными контактами; кого не добавили из-за приватности —
  сообщение) / «New channel» (`createNewSupergroupChat`). Моя роль — `role(chat)` из
  `PresenceStore.group_status`; QML-привязки к `role()`/`inviteLink()` зависят от `revision`.
  Владельцу/админу в профиле: инвайт-ссылка (из full info, нет — `replacePrimaryChatInviteLink`),
  «Add members» (`addChatMembers`), правка имени/описания, удаление (владелец, `deleteChat`);
  правый клик по участнику — админ (`setChatMemberStatus` с `ADMIN_RIGHTS`, без права
  назначать других) / снять / удалить (`banChatMember`).
- Приватность/хранилище (`PrivacyController` = `privacy`, грузится при открытии настроек):
  правило «All/Contacts/Nobody» заменяет только главное правило, исключения для людей и чатов
  сохраняются (`with_main_rule`); уведомления по типам чатов — `setScopeNotificationSettings`
  (`mute_for` 0 / год); «Clear media cache» — `optimizeStorage` с нулевыми лимитами.
- Контакты: кнопка-человек в сайдбаре → `ContactsDialog` (`ContactsModel` = `contacts`):
  `getContacts`/`searchContacts`, онлайн сверху, клик — `createPrivateChat` → открыть;
  «Add» — `importContacts` по номеру (нет в Telegram — ошибка); в профиле не-контакта —
  «Add contact» (`addContact`).
- Меню чата в списке (правый клик): `ChatActions` (`chatActions` в QML) — закрепить
  (`toggleChatIsPinned` в текущем списке), mute на 1 ч / 8 ч / 2 дня / навсегда
  (`setChatNotificationSettings`: полный объект настроек чата из `Chat.notification_settings`,
  меняется только `mute_for`), архив (`addChatToList`), прочитано (`viewMessages` последнего +
  `readAllChatMentions`) / непрочитано (`toggleChatIsMarkedAsUnread`, точка в списке),
  очистить историю и выйти/удалить через `ConfirmDialog` («также для …» — `revoke`).
- Закрепы: `store/pinned.py` (`PinnedMessages`, Qt-free) грузит все закреплённые
  (`searchChatMessages` с `searchMessagesFilterPinned`, запасной — `getChatPinnedMessage`) и
  обновляется по `updateMessageIsPinned`/удалениям. Полоса под шапкой показывает одно; клик —
  `messages.nextPinned()` отдаёт показанное (QML прыгает к нему) и переходит к следующему
  старшему по кругу. Pin/Unpin и «Copy link» в меню — по `can_be_pinned`/`can_get_link` из
  `getMessageProperties`.
- Превью ссылок: `store/link_preview.parse` (linkPreview: фото, обложка/миниатюра видео,
  chatPhoto у ссылок на людей/чаты) → роль `linkPreview` (карточка под текстом; картинка сбоку
  или во всю ширину при `show_large_media`). В поле ввода `ComposerModel` спрашивает
  `getLinkPreview` через 0.6 с после смены ссылки; «×» — `sendOptions().noPreview` →
  `link_preview_options.is_disabled`. Текст из Composer уходит через `messages.sendMessage(text,
  replyTo, composerModel.sendOptions())`.
- @упоминания: «@…» перед курсором в группе → `composerModel.findMentions` (`searchChatMembers`,
  без себя, до 8) → попап над полем (↑/↓, Enter/Tab, клик). С username вставляется
  `@username`, без него — `[Имя](tg://user?id=N)`: после `parseMarkdown` `richtext.mention_names`
  превращает такую ссылку в `textEntityTypeMentionName` (человек получает упоминание).
- Тихая и отложенная отправка: правый клик по «Send» → «Send without sound» / «Schedule
  message…» (`ScheduleDialog`: быстрые варианты или дата и время) → `send({silent} |
  {scheduleAt})` → `messageSendOptions`. Отложенные не попадают в ленту (`ChatHistory._mine`
  отсекает `scheduling_state`); при `has_scheduled_messages` в шапке часы → `ScheduledList`
  (`getChatScheduledMessages`; отправить сейчас = `editMessageSchedulingState(null)`,
  перенести, удалить).
- Отправка: текст проходит через TDLib `parseMarkdown` (**bold**, __italic__, `code`, ```pre```,
  ~~strike~~, ||spoiler||, [text](url)), при ошибке уходит как plain text.

## LLM и приватность (решено)

- Генерация через OpenRouter (локальные LLM генерируют плохо — отвергнуто автором).
  Эмбеддинги для поиска можно считать локально.
- LLM-обработка включается для каждого чата отдельно, по умолчанию выключена.
- Секретные чаты в LLM не отправляются никогда.
- В OpenRouter использовать только провайдеров с zero data retention.
- Пользователю явно показывать в настройках, что и куда уходит.
- Каждое саммари содержит ссылки на исходные сообщения.

## Упаковка (M6, подробно в `packaging/README.md`)

- macOS: `.dmg` arm64 и x86_64 (GitHub Actions `macos-15` / `macos-15-intel`). Членство Apple
  Developer неактивно: ad-hoc подпись без нотаризации (пользователю — «Open Anyway»). Для
  публичного релиза — продлить членство и нотаризовать. Не раздавать широко сборки под чужим
  Team ID — при смене Team ID у пользователей сбросятся разрешения и доступ к Keychain.
- Linux: AppImage и Flatpak, оба из одной PyInstaller-сборки, собранной в Ubuntu 22.04.
- Сборочный Python — только python-build-standalone через uv (`setup_build_env.sh`), не
  Homebrew: тот собран под macOS машины сборки (`_sqlite3` требовал macOS 26). На Linux он же
  даёт свежий SQLite (FTS5 `contentless_delete` нужен 3.43+, в Ubuntu 22.04 — 3.37).
- Релизные версии в `packaging/constraints.txt`: PySide6 6.9.3 (у 6.10/6.11 биндинги macOS
  собраны под 15.0 при теге колеса 13.0), onnxruntime 1.23.2 (реально macOS 13.4+). На macOS
  колёса выбираются под `MACOSX_DEPLOYMENT_TARGET` (`--python-platform`), иначе numpy берёт
  колесо под macOS 14. Тесты должны проходить и на Qt 6.9.3 (CI гоняет их на constraints).
- `TG_API_ID`/`TG_API_HASH` вшиваются в сборку (`_build.py`, генерируется из окружения или `.env`
  репо, не в git; в CI — из секретов). Ключ OpenRouter не вшивается никогда: пользователь вводит
  свой в Settings (проверка `GET /api/v1/key`, сохранение в конфиг-`.env` с правами 0600,
  применяется без перезапуска через `AiService.use_router`). `.env` ищется в CWD и в
  конфиг-папке пользователя (`config_env_path()`); переменная окружения главнее.
- Id приложения `io.github.tgclient.TgClient` (`config.APP_ID`) — поменять под реальный GitHub.
- Лицензия Qt/PySide6: LGPLv3, динамическая линковка (onedir, Qt-библиотеки отдельными файлами).

## Этапы

M0–M5 работают на живом аккаунте. Пометки «на реальном TDLib не запускалось» у остальных
этапов снимать после проверки их функций вживую.

- **M0** — скелет, сборка TDLib, авторизация, консольный вывод чатов. *Написано, покрыто тестами
  на FakeLib; работает на живом аккаунте.*
- **M1** — окно: логин в GUI, папки-вкладки (+ Archive) со счётчиками, список чатов с аватарками,
  превью, временем, непрочитанным, упоминаниями, mute, анимацией перемещения, подгрузкой при
  скролле, светлая/тёмная тема. *Написано, тесты + offscreen-рендер QML проходят; работает на
  живом аккаунте.*
- **M2** — лента: подгрузка истории вверх без прыжков, пузыри с группировкой и аватарками в
  группах, метки дней, сервисные сообщения, форматирование (entities → rich text, выделение
  текста, клик по ссылкам), ответы (с подгрузкой цитаты), правки, удаления, статусы
  отправки/прочтения, отправка с markdown, отметка прочитанного, кнопка «вниз».
  *Написано, тесты + offscreen-рендер проходят; работает на живом аккаунте.*
- **M3** — медиа: фото и видео/GIF (превью со скруглением, плейсхолдер из minithumbnail,
  длительность, прогресс, открытие во внешнем приложении), стикеры без пузыря (WebP; TGS/WebM
  — статичной миниатюрой), круглые видео, файлы и аудио (карточка: скачать/отменить/открыть,
  прогресс), голосовые (плеер, волна, перемотка кликом, отметка «прослушано»), время поверх
  медиа или рядом с карточкой, отправка файлов (кнопка «+», drag-and-drop; картинки уходят
  фото, остальное документом), «Show in folder». *Написано, тесты + offscreen-рендер
  проходят; работает на живом аккаунте.*
- **M4** — AI через OpenRouter (только ZDR-провайдеры): переключатель «AI on/off» в шапке чата
  с подтверждением (что и куда уйдёт), саммари (непрочитанное / 24 часа / 7 дней) в боковой
  панели со ссылками-временем на исходные сообщения и прыжком к ним с подгрузкой истории,
  транскрипция голосовых по клику (аудио Ogg/Opus как `input_audio`), окно настроек
  (тема, ключ, модели, «что и куда уходит», список чатов с AI). Ключ — в Settings (см.
  «Упаковка»), для разработки можно `OPENROUTER_API_KEY` в `.env`; модели — `TGC_SUMMARY_MODEL` / `TGC_TRANSCRIPTION_MODEL` (по умолчанию
  `google/gemini-2.5-flash`). *Написано, тесты на FakeLib + фейковом OpenRouter
  (httpx.MockTransport) + offscreen-рендер проходят; с реальным OpenRouter не запускалось —
  проверить, что у модели есть ZDR-эндпоинт и что она принимает ogg.*
- **M5** — поиск в сайдбаре (Cmd/Ctrl+F): чаты по названию + сообщения из гибридного поиска
  (локальный FTS5 + локальные эмбеддинги `paraphrase-multilingual-MiniLM-L12-v2` + серверный поиск
  TDLib), подсветка совпадений, значок «найдено по смыслу», переход к сообщению с подгрузкой
  истории и вспышкой. Индексация в фоне, статус и переключатель смыслового поиска в настройках.
  Вместе с M5: саммари человека (клик по имени/аватарке в группе → меню Reply / Summarize this
  person; в личке — «About …» в меню Summarize; пункт и в меню сообщения), новые контекстные
  меню, свой набор иконок. *Написано, тесты на FakeLib + FakeEmbedder + offscreen-рендер
  проходят; SearchService проверен с настоящей моделью (ru/cs/en запросы находят друг друга);
  работает на живом аккаунте; темп backfill и FLOOD_WAIT на большом аккаунте — присмотреться.*
- **M6** — упаковка и CI (`packaging/README.md`): TDLib на зафиксированном коммите со статическим
  OpenSSL 3.5 из исходников; PyInstaller onedir; `.dmg` (arm64 и x86_64, ad-hoc подпись, минимум
  macOS 13.4 считается по всем бинарникам), AppImage (контейнер Ubuntu 22.04, glibc 2.35+),
  Flatpak (обёртка над той же сборкой); `tgclient --self-test` в каждой сборке; своя иконка;
  GitHub Actions (тесты → сборки → черновик релиза по тегу `v*`). *Локально проверены: macOS
  arm64 .dmg (монтируется, подпись валидна, self-test изнутри проходит); Linux aarch64:
  AppImage (self-test на чистой Fedora 42) и Flatpak (собран образом из CI, self-test внутри
  песочницы). GitHub Actions не запускались: у репо нет remote; workflow проверен actionlint.
  x86_64-сборки (macOS Intel, Linux) — только в CI. Linux aarch64 требует glibc 2.39 (колёса
  PySide6), поэтому его контейнер — Ubuntu 24.04.*
- **M7** — основной клиент: системные уведомления (TDLib notification API, учёт mute, клик
  открывает чат, снятие после прочтения, настройки: вкл/выкл и скрытие текста) и бейдж
  непрочитанного на иконке; разделитель «Unread messages» и открытие чата на первом
  непрочитанном; облачные черновики (синхронизация, «Draft:» в списке); правка (Up в пустом
  поле, Esc — отмена), удаление (с «удалить у всех»), пересылка (выбор чата); реакции (показ,
  клик — поставить/снять, быстрые реакции в меню); «печатает…» в шапке и списке, онлайн /
  «был(а)…» / участники в шапке, точка онлайна на аватарке, свой typing и `online`; вложения
  через окно с подписью, вставка картинки или файлов из буфера (Cmd/Ctrl+V); «Forwarded from»
  в пузыре; тема и настройки уведомлений сохраняются. *Написано, тесты на FakeLib +
  offscreen-рендер проходят (и на Qt 6.9.3); на реальном TDLib не запускалось. Системные
  уведомления не проверены вживую: macOS UserNotifications — только из .app (проверить
  запрос разрешения и клик), Linux D-Bus — на реальном сервере уведомлений и во Flatpak.*

- **M8** — AI поверх саммари (всё через `AiService`): перевод входящих по клику (под текстом,
  кэш, язык в Settings) и своего текста на RU/UK/CS/EN с превью и отправкой только кнопкой;
  панель AI чата — вопросы к истории, «Даты и встречи» с «Add to calendar» (.ics), сбор ответов
  на сообщение (таблица + «No answer»), вопросы к PDF/текстовому файлу; окно «Digest / What did
  I promise» из сайдбара со ссылками в нужный чат; умные уведомления (флаг на чат с
  подтверждением); подсказка ответа (4 тона) в поле ввода; стоимость в панели, лимит на чат в
  месяц, расходы по чатам, кэш, выбор трёх моделей в Settings, «Delete AI data» для чата.
  Потом: «Explain» и «Suggest reply» в меню сообщения (контекст: цепочка ответов, ±20 сообщений,
  саммари, свой стиль; стратегии ответа с переводом, Insert / Another / Shorter / Formal,
  стриминг в панель).
  Доработки после живого использования: единые метки времени у ссылок (с датой для не
  сегодняшних, `#2` для совпадающих), саммари чата — выводы с разделом «Касается тебя»
  (разметка you/↩you/@you в коде), Explain с `kind` (лёгкие сообщения — реакции и реплика);
  второй круг: «Касается тебя» отбирает код, ссылки — сноски ¹ ² (≤2 на утверждение,
  подсказка «кто, когда»), таблица ответов `Хто | Позиція | Відповідь` без ботов и без «Без
  ответа» в больших группах, «I also read» (без перевода ответов на читаемых языках),
  t.me-ссылки на сообщения открываются в клиенте.
  Заодно: на macOS `uv run` идёт из dev-бандла (правильная иконка и имя в Dock и запросе
  разрешения на уведомления). *Написано, тесты на FakeLib + фейковом OpenRouter (с `usage`) +
  offscreen-рендер проходят (и на Qt 6.9.3); с реальным OpenRouter не запускалось — проверить
  ZDR-эндпоинты у `gemini-2.5-flash-lite`, PDF через `file-parser`/`native` при ZDR, поле
  `usage.cost`, качество JSON у «Даты и встречи».*
- **M9** — медиа и аккаунты: встроенный просмотрщик (фото с зумом и панорамой, видео с
  управлением, ←/→ по медиа чата, подпись, «Save as…», открыть/показать в папке), альбомы
  сеткой с подписью, анимированные стикеры TGS (rlottie) и WebM с альфой (PyAV/libvpx) и GIF
  в ленте, кастомные эмодзи в тексте и реакциях, запись и отправка голосовых (кнопка
  микрофона, уровень, таймер, «записывает голосовое…»), вход по QR, несколько аккаунтов
  (переключатель в сайдбаре, добавить/отменить/выйти, уведомления и бейдж со всех),
  автообновление из GitHub Releases. *Написано, тесты на FakeLib (в т.ч. два аккаунта на одном
  хабе, сгенерированные TGS/WebM/Opus) + offscreen-рендер проходят (и на Qt 6.9.3 + PyAV 15.1);
  на реальном TDLib не запускалось. Не проверены вживую: микрофон (offscreen его нет) и запрос
  доступа на macOS, QR со сканированием телефоном, GIF через `VideoOutput` (в offscreen-тесте
  кадр не рисуется), обновление (у репо нет remote и релизов).*

### План

Отставание от официального клиента по приоритетам (P0–P3) — `docs/FEATURE_GAPS.md`: идём по
нему сверху вниз, один пункт — один коммит, закрытый пункт отмечать там `[x]`.
P0 закрыт (последним — профиль человека без «Кто это»).

Потом — то, что нужно друзьям (M10). Детали и мелочи — в «TODO».

- **M10 — для друзей.** Плагины на Python (API поверх `services/` и событий TDLib), правила как в
  почте (автоархив, автомьют, пересылка по условию), командная палитра Cmd/Ctrl+K и полное
  управление с клавиатуры, локальные заметки и теги на людях.
- Вне скоупа: звонки, Stories, мини-приложения.

## Запуск

```bash
uv sync --extra semantic          # без --extra semantic — поиск только по словам
./scripts/build_tdlib.sh          # долго; на Linux сначала поставить зависимости из скрипта
                                  # сборка .dmg/AppImage — см. packaging/README.md
cp .env.example .env              # вписать TG_API_ID / TG_API_HASH с my.telegram.org
                                  # (ключ OpenRouter — в Settings или OPENROUTER_API_KEY в .env)
uv run tgclient-cli               # консоль: логин + список чатов
uv run tgclient                   # GUI
uv run python -m unittest discover -s tests
```

## TODO / открытые вопросы

- Упаковка: ключ OpenRouter хранится в конфиг-`.env` (0600), не в Keychain — ad-hoc подпись
  меняется с каждой сборкой и Keychain спрашивал бы доступ заново; при Developer ID —
  перейти на keyring. Нотаризация при активном
  членстве Apple; тихая замена .app при обновлении (после Developer ID); aarch64-сборки Linux
  в CI; урезать бандл (PySide6 ~130 МБ, onnxruntime ~70 МБ, PyAV ~40 МБ); Flathub (нужен свой
  домен/GitHub для app id).
- Мультиаккаунт: порядок аккаунтов перетаскиванием, Cmd/Ctrl+1…5, отдельные настройки на
  аккаунт (сейчас prefs общие), выход из всех сразу.
- Уведомления: ответ прямо из уведомления (macOS `UNTextInputNotificationAction`, Linux
  `inline-reply`), звук по чату (`notification_sound_id`), уведомления о реакциях на свои
  сообщения и о звонках; бейдж во Flatpak (Unity LauncherEntry через песочницу) не проверен.
- Сообщения: пересылка с подписью/без автора, кастомные эмодзи в реакциях (сейчас ✦),
  права на реакции/правку из `chat.permissions`.
- Поиск: фильтры (чат, человек, дата, тип медиа) в UI — сервис уже умеет `chat_id`/`sender`;
  «ещё результаты» (пагинация серверного поиска); индексация по запросу «проиндексировать весь чат»; переиндексация при смене
  модели эмбеддингов; удалить индекс из настроек; порог смыслового поиска — подобрать на
  реальных данных (сейчас 0.3).
- Логин: выделять введённый код после ошибки (QR — в M9).
- Медиа: отправка файлов альбомом (`sendMessageAlbum`; сейчас по одному, подпись — у первого);
  стриминг видео в просмотрщике до полной загрузки; кружки (videoNote) воспроизводить в ленте;
  выделение файла в Finder/Nautilus при «Show in folder»; анимированные кастомные эмодзи (сейчас
  первый кадр); скругление углов GIF (VideoOutput без шейдеров не клипуется); в пикере —
  оттенки кожи эмодзи, избранные стикеры, анимированные стикеры в сетке, поиск стикеров по
  эмодзи; лимиты автозагрузки (размер, мобильная сеть); пауза/предпрослушка записи
  голосового и кружка; запись кружка не проверена с настоящей камерой.
- Лента: черновики с форматированием (сейчас plain text с markdown-разметкой);
  Pre как отдельный блок; цитаты с полосой слева; разбивка сообщений длиннее 4096 символов.
- AI: ключ OpenRouter в keyring вместо конфиг-`.env` (после Developer ID, см. «Упаковка»);
  транскрипция кружков (видео → аудио);
  саммари длинных чатов по частям (сейчас обрезка до 1500 сообщений / 200k символов, берутся
  самые новые); дайджест по
  расписанию (сейчас по клику); сбор ответов по треду (сейчас — сообщения после вопроса);
  вопросы к .docx/.xlsx; «Translate» для голосовых без транскрипта; лимит отдельно на чат
  (сейчас один на все); перевод с сохранением форматирования (entities).
- Context properties → со временем перейти на `QML_ELEMENT`-регистрацию / required properties,
  если понадобится qmllint/qmlcachegen.

