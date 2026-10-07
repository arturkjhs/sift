# Отставание от официального клиента и приоритеты

Список того, что умеет официальный Telegram Desktop, а tgclient пока нет, по приоритетам.
Статусы собраны по `CLAUDE.md` от 2 октября 2026 и скриншотам с живого аккаунта — часть
пунктов могла быть сделана позже.

## Как работать с этим файлом (для Claude Code)

- Перед каждым пунктом проверь код: если функция уже есть — отметь `[x]`, допиши одной
  строкой, где она реализована, и переходи к следующему. Пункты с пометкой **проверить**
  статус неизвестен.
- Один пункт — один коммит: тесты на `FakeLib` (и `QAbstractItemModelTester` для моделей),
  offscreen-скриншот для UI (светлая и тёмная тема).
- Сигнатуры методов TDLib сверяй с `vendor/td/td/generate/scheme/td_api.tl` на нашем
  зафиксированном коммите: в разных версиях TDLib поля переименовывались (например,
  `message_thread_id` → `topic_id`, `web_page` → `link_preview`). Названия методов ниже —
  ориентир, не контракт.
- Все инварианты из `CLAUDE.md` действуют. Особенно: TDLib — источник истины, AI-правила
  только в `AiService`, id чатов в QML — `var`.
- Закрыл пункт — отметь `[x]` здесь и обнови «Этапы» в `CLAUDE.md`.

Обозначения: **P0** — сначала, **P1** — нужно для ежедневного использования,
**P2** — комфорт, **P3** — потом. В конце — что вне скоупа.

---

## P0. Исправления AI из ревью скриншотов

Не отставание от официального клиента, а дефекты уже сделанного. Если что-то уже исправлено —
отметить и пропустить.

- [x] **Саммари человека.** Убрать раздел «Хто це». о чём человек пишет,
  в чём разбирается и чем может помочь, что связывает его с пользователем (вопросы друг
  другу, открытые договорённости).
  → `summary.PERSON_PROMPT`: «О чём пишет» / «В чём разбирается и чем может помочь» / «Между вами»; последний оставляет код (`keep_for_you` по ↩you/@you и своим репликам в обмене, `AiService._run_person`).
- [x] **«Касается тебя» только по маркерам.** Раздел собирается в коде из строк с `↩you` и
  `@you`; модель только пересказывает отобранное. Вопрос «ко всем» сюда не попадает.
  → `summary.reader_marks` + `summary.keep_for_you` (M8, второй круг).
- [x] **Ссылки-время → номера.** Не больше 2 ссылок на утверждение; рендер маленькими
  номерами `¹ ²` с подсказкой при наведении «Автор, 02.10 19:56». Убирает шум и суффиксы `#2`.
  → `summary.number_links`, `store/markdown.py`, `AiController.linkTooltip` (M8).
- [x] **Таблица «Answers».** Колонки Хто / Позиція / Відповідь (сейчас в «Хто» стоит
  позиция). Боты (`userTypeBot`) исключаются всегда. «Без ответа» показывать только при
  понятном списке адресатов: чат до ~30 участников или упомянутые в вопросе.
  → `assist.answers_prompt`, `assist.drop_no_answer`, `SMALL_GROUP` = 30 (M8).
- [x] **Язык перевода в вариантах ответа.** Настройка «языки, которые я читаю». Вариант на
  одном из них — без перевода; иначе перевод на первый язык из списка, не на язык UI.
  → «I also read» (`read_languages` в prefs, `AiService.reads`); перевод — на язык AI-ответов (M8).

---

## P1. Без этого клиент нельзя сделать основным

- [x] **Системные уведомления + счётчик в Dock/трее.**
  Новое входящее сообщение (не своё, чат не замьючен с учётом настроек по умолчанию, чат
  не открыт или окно не в фокусе) → уведомление с именем, чатом и превью; клик открывает
  чат на этом сообщении. Счётчик непрочитанного — из `updateUnreadMessageCount` /
  `updateUnreadChatCount`. macOS: UserNotifications (через PyObjC или `QSystemTrayIcon`
  как минимум), бейдж — `NSApp.dockTile`; Linux: `org.freedesktop.Notifications`, бейдж —
  `com.canonical.Unity.LauncherEntry`. Звук — настройкой. Без отправки текста уведомлений
  куда-либо кроме ОС.
  *Готово, когда:* пропущенных сообщений не бывает при свёрнутом окне; клик ведёт в чат.
  → M7: `store/notifications.py`, `ui/notifications.py` (UserNotifications / D-Bus), бейдж `setBadgeNumber`; звук — переключатель «Play a sound» (`notification_sound`).

- [x] **Разделитель «Непрочитанные» и открытие на первом непрочитанном.**
  По `chat.last_read_inbox_message_id`: история грузится вокруг этого id
  (`getChatHistory` с отрицательным `offset`), лента позиционируется на разделитель.
  Понадобится «разрыв» в `ChatHistory` (загрузка вокруг id, а не только от новейшего) —
  он же решает TODO про прыжок к очень старой цитате. Кнопка «вниз» показывает число
  непрочитанных.
  → `ChatHistory.load_around/load_newer/load_latest` (окно без новейших, `atLatest`), `unreadCount` на кнопке «вниз» (`JumpButton.qml`).

- [x] **Кнопка «к следующему упоминанию» / реакции на мои сообщения.**
  `searchChatMessages` с фильтром непрочитанных упоминаний (и реакций), `readAllChatMentions`.
  Бейдж @ над кнопкой «вниз».
  → `messages.nextMention/nextReaction/readAllMentions/readAllReactions`, кнопки «@» и ♡ над «вниз».

- [x] **Редактировать, удалить, переслать свои сообщения.**
  Редактирование: `editMessageText` / `editMessageCaption`, проверка прав через свойства
  сообщения (`getMessageProperties` или поля `can_be_edited` — по нашей версии TDLib);
  в UI — режим редактирования в `Composer`, ↑ в пустом поле редактирует последнее.
  Удаление: `deleteMessages` с выбором «у всех / у себя» (`revoke`), подтверждение.
  Пересылка: выбор чата (переиспользовать поиск чатов), `forwardMessages`.
  Мультивыбор сообщений (Shift/Cmd-клик) для удаления и пересылки пачкой.
  → M7 (правка, удаление с `revoke`, пересылка) + мультивыбор: роль `selected`, панель вместо поля ввода (`MessageView.qml`).

- [x] **Реакции полностью.** Клик по реакции под сообщением — поставить/снять
  (`addMessageReaction` / `removeMessageReaction`); пикер из `getMessageAvailableReactions`
  в меню сообщения и при наведении. Показ, кто поставил (в маленьких чатах).
  → M7 + кнопка реакции при наведении на сообщение (`reactButton` → `ReactionPicker`), подсказка «кто поставил» на плашке (`recent_sender_ids`, полный список — `getMessageAddedReactions`, `messages.reactorsText`).

- [x] **Превью ссылок.** Карточка под текстом: сайт, заголовок, описание, картинка
  (`link_preview` или `web_page` в `messageText`, по версии TDLib). Картинка — через
  `image://tg/media`. Отключение превью при отправке своего сообщения.
  → `store/link_preview.py`, роль `linkPreview` + `linkCard` в `MessageDelegate.qml`; в поле ввода — превью через `getLinkPreview` и «убрать» (`ComposerModel.linkPreview/sendOptions`).

- [x] **Закреплённое сообщение в шапке чата.** `getChatPinnedMessage`, полоса под шапкой,
  клик — прыжок к сообщению; при нескольких закрепах — переключение по кругу.
  → `store/pinned.py` (`searchMessagesFilterPinned`, `updateMessageIsPinned`), полоса `pinnedBar`, Pin/Unpin в меню сообщения (`pinDialog`), «Unpin all»; заодно «Copy link» (`getMessageLink`).

- [x] **Темы (форумы) в супергруппах.** Для `is_forum`: список тем (`getForumTopics`),
  история темы (`getMessageThreadHistory` или аналог), отправка в тему, непрочитанное
  по темам. Без этого крупные комьюнити-чаты с темами читаются как каша.
  AI-саммари — по теме, а не по всему чату.
  → `store/forums.py`, `models/topics.py` + `TopicList.qml`, `ChatHistory(topic_id=)`, отправка/черновики/typing с `topic_id`, саммари темы (`topic:<id>`).

- [x] **Поиск внутри открытого чата.** Cmd/Ctrl+F в чате (сайдбар-поиск остаётся по всему):
  `searchChatMessages`, стрелки вверх/вниз, подсветка, прыжок через существующий `jumpTo`.
  → `messages.searchInChat/searchOlder/searchNewer`, полоса `chatSearch` в `MessageView.qml`, `richtext.highlight_html`; глобальный поиск — Cmd/Ctrl+Shift+F.

- [x] **`t.me`-ссылки внутри приложения.** `getInternalLinkType`: ссылка на сообщение →
  `getMessageLinkInfo` и прыжок; на публичный чат → `searchPublicChat` и открытие;
  инвайт → `checkChatInviteLink` с превью и кнопкой «вступить» (`joinChatByInviteLink`).
  Также `tg://`-ссылки из rich text (упоминания, хэштеги).
  → `store/links.py` (`getInternalLinkType`: сообщения, публичные чаты/боты, телефоны, инвайты с окном «Join», `tg://user`, хэштеги → поиск); «Join» вместо поля ввода в чужом чате; права на запись в каналах/группах.

- [x] **Черновики с синхронизацией.** `setChatDraftMessage` при уходе из чата и по таймеру,
  восстановление из `chat.draft_message` / `updateChatDraftMessage`, «Draft: …» красным
  в превью списка. Черновик с ответом сохраняет `reply_to`.
  → M7: `ComposerModel` (`setChatDraftMessage`, `draft_reply_to`), «Draft:» в списке.

- [x] **Шифрование локальной базы + пароль на приложение.**
  Ключ `database_encryption_key` генерировать и хранить в системном keyring; для уже
  существующей базы — `setDatabaseEncryptionKey`. Локальный пароль: блокировка окна
  по запросу и после N минут неактивности; AI-данные (`AiStore`, индекс поиска)
  под той же защитой или в зашифрованном виде. Клиент хранит переписку и саммари на диске
  и раздаётся друзьям — это не P3.
  → `vault.py` (ключ базы в keyring, перешифровка старой базы, пароль scrypt+AES-GCM, `*.sealed` AI-данные), `ui/lock.py` + `LockScreen.qml`/`LockWindow.qml`, раздел Passcode в настройках, авто-блокировка, Cmd+L.

---

## P2. Комфорт

- [x] **Меню чата в списке:** закрепить, mute на время, в архив, отметить прочитанным /
  непрочитанным, очистить историю, выйти (`toggleChatIsPinned`, `setChatNotificationSettings`,
  `addChatToList`, `toggleChatIsMarkedAsUnread`, `deleteChatHistory`, `leaveChat`).
  **проверить** — может быть частично сделано.
  → `ui/chat_actions.py` (`chatActions`), меню `chatMenu` в `MainView.qml`, `ConfirmDialog.qml`, точка «отмечен непрочитанным».
- [x] **«Печатает…» и онлайн.** `updateChatAction` → шапка и превью в списке;
  `sendChatAction` при наборе; `updateUserStatus` → «был(а) …» в шапке лички.
  **проверить** онлайн в шапке лички.
  → M7: `store/presence.py`, шапка и список; свой typing в `ComposerModel`.
- [x] **Иконки в списке чатов:** pinned, muted, verified, ✓/✓✓ у своего последнего
  сообщения. Перевести оставшиеся глифы на набор `ui/icons.py`.
  → роли `verified`/`outStatus` (+ `pinned`/`muted`) в `ChatListModel`, иконки в `ChatDelegate.qml`; статусы в пузыре, плеер голосовых, карточка файла, просмотрщик, переключатель аккаунтов — на `ui/icons.py`.
- [x] **Автодополнение @упоминаний.** `searchChatMembers` при вводе `@`; для людей без
  username — сущность `textEntityTypeMentionName` (собирать entities вручную, `parseMarkdown`
  её не создаёт).
  → `ComposerModel.findMentions/mentions`, попап `mentionPopup` в `Composer.qml`; без username — `[Имя](tg://user?id=N)` → `richtext.mention_names` после `parseMarkdown`.
- [x] **Вставка картинки из буфера и подпись к файлам.** Cmd/Ctrl+V с изображением → окно
  отправки с превью и полем подписи; то же окно для «+» и drag-and-drop.
  → M7: `ComposerModel.staged`, `SendFilesDialog.qml`.
- [x] **Профиль чата и человека.** Панель: аватар, описание, username, участники
  (`searchChatMembers`), общие группы, вкладки медиа / файлы / ссылки / голосовые
  (`searchChatMessages` с фильтрами). Кнопки mute, поиск, AI.
  → `models/profile.py` + `ProfilePanel.qml` (клик по заголовку, «View profile» у человека); AI — кнопки в шапке чата.
- [x] **Встроенный просмотрщик фото и видео.** Полноэкранный оверлей, листание по чату,
  зум; видео — QtMultimedia `VideoOutput` внутри приложения.
  → M9: `MediaViewer.qml`, `models/viewer.py`.
- [x] **Альбомы.** Группировка по `media_album_id` в одну сетку-пузырь.
  → M9: `store/album.py`.
- [x] **Опросы и чек-листы:** рендер вариантов, голосование (`setPollAnswer`), результаты;
  чек-листы — отметка пунктов.
  → `store/polls.py`, `PollContent.qml`, `messages.vote/markTask`, `updatePoll` в `ChatHistory`.
- [x] **Кнопки ботов.** Inline-клавиатура (`getCallbackQueryAnswer`, url-кнопки), обычная
  клавиатура бота над полем ввода.
  → `store/keyboards.py`, роль `inlineKeyboard`, `messages.pressButton/replyKeyboard/sendKeyboardButton`, тост и окно ответа бота.
- [x] **Комментарии к постам каналов** (обсуждение через связанную группу).
  → роль `comments` + полоса в пузыре, `messages.openComments/closeComments`, `ChatHistory(thread_id=)`.
- [x] **Отложенная и тихая отправка.** `messageSendOptions`: `disable_notification`,
  `scheduling_state` с датой; список отложенных.
  → меню кнопки «Send», `ScheduleDialog.qml`, `ScheduledList.qml`, `messages.loadScheduled/reschedule/sendScheduledNow/deleteScheduled`.
- [x] **Активные сессии.** `getActiveSessions`, `terminateSession`, «завершить все другие».
  → `ui/devices.py` (`devices`), раздел Devices в настройках (обновляется при открытии).
- [x] **Вход по QR.** `requestQrCodeAuthentication`, состояние
  `authorizationStateWaitOtherDeviceConfirmation` → QR в окне логина.
  → M9: `AuthController`, `image://qr/`.

---

## P3. Потом

- [x] Мультиаккаунт (`TdHub` готов; раздельные `data_dir`, переключатель в UI).
  → M9: `ui/accounts.py`, `AccountSwitcher.qml`.
- [x] Создание и редактирование папок (`createChatFolder`, `editChatFolder`,
  `reorderChatFolders`); архив строкой вверху списка вместо вкладки.
  → `ui/folders.py` + `FolderEditorDialog.qml`, меню вкладок в `FolderTabs.qml`; строка «Archived chats» в `MainView.qml`.
- [x] Анимированные стикеры (rlottie для TGS, WebM через QtMultimedia), GIF с автоплеем,
  кастомные эмодзи (`getCustomEmojiStickers`).
  → M9: `ui/animation.py`, `store/custom_emoji.py` (анимированные кастомные эмодзи — первым кадром).
- [x] Поиск и отправка GIF, создание опросов, запись кружков.
  → `models/gifs.py` (вкладка GIFs, @gif), `PollEditor.qml` + `messages.sendPoll`, `ui/video_note.py` (камера → H.264/AAC MP4).
- [x] Контакты: список, поиск, добавление.
  → `models/contacts.py` + `ContactsDialog.qml`, «Add contact» в профиле.
- [x] Создание групп и каналов, инвайт-ссылки, базовая админка (права, бан, удаление).
  → `ui/group_admin.py` (`groupAdmin`), `NewChatDialog.qml`, инструменты админа в `ProfilePanel.qml`.
- [x] Настройки приватности (`getUserPrivacySettingRules` / `setUserPrivacySettingRules`),
  уведомлений по типам чатов, хранилище и очистка кэша (`getStorageStatistics`,
  `optimizeStorage`).
  → `ui/privacy.py` (`privacy`): разделы Privacy/Storage и уведомления по типам чатов в настройках.
- [ ] Прокси (SOCKS5, MTProto: `addProxy`, `enableProxy`).
- [ ] Локализация интерфейса RU/UA/CZ (строки уже в `qsTr()`).
- [x] Транскрипция через TDLib `recognizeSpeech` для Premium-аккаунтов (бесплатно,
  без OpenRouter).
  → `AiService._recognize` (при `premium`), кнопка Transcribe и без «AI on».

---

## Вне скоупа

Звонки и групповые звонки, трансляции, Stories, мини-приложения, Telegram Stars и платежи,
подарки, Business-функции.

---

## Где tgclient уже сильнее (не сломать)

Саммари любого чата за период со ссылками на исходные сообщения и разделом «Касается тебя»;
разбор сообщения в контексте и варианты ответа разными стратегиями; сбор ответов в группе;
транскрипция голосовых без лимитов; гибридный поиск по смыслу между RU/UA/CZ; видимые модель
и цена каждого AI-запроса. При правках P1–P3 эти функции должны продолжать работать —
соответствующие тесты гонять всегда.
