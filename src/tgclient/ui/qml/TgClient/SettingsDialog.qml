import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts

// App settings. The AI section spells out exactly what leaves the computer and where it goes.
Popup {
    id: root
    objectName: "settingsDialog"
    parent: Overlay.overlay
    anchors.centerIn: parent
    width: Math.min(560, parent.width - 48)
    height: Math.min(flick.contentHeight + topPadding + bottomPadding, parent.height - 48)
    modal: true
    padding: 22

    background: Rectangle {
        radius: 12
        color: Theme.sidebar
        border.width: 1
        border.color: Theme.separator
    }

    component SectionTitle: Text {
        Layout.fillWidth: true
        Layout.topMargin: 6
        color: Theme.text
        font.pixelSize: Theme.fontTitle
        font.weight: Font.DemiBold
    }

    component Paragraph: Text {
        Layout.fillWidth: true
        wrapMode: Text.Wrap
        color: Theme.text
        font.pixelSize: Theme.fontBody
        lineHeight: 1.1
    }

    component Bullet: RowLayout {
        property alias text: bulletText.text
        Layout.fillWidth: true
        spacing: 8
        Text {
            Layout.alignment: Qt.AlignTop
            text: "\u2022"
            color: Theme.textMuted
            font.pixelSize: Theme.fontBody
        }
        Text {
            id: bulletText
            Layout.fillWidth: true
            wrapMode: Text.Wrap
            color: Theme.text
            font.pixelSize: Theme.fontBody
            lineHeight: 1.1
        }
    }

    contentItem: Flickable {
        id: flick
        clip: true
        contentWidth: width
        contentHeight: column.implicitHeight
        boundsBehavior: Flickable.StopAtBounds
        ScrollBar.vertical: ScrollBar {}

        ColumnLayout {
            id: column
            width: flick.width
            spacing: 10

            RowLayout {
                Layout.fillWidth: true
                Text {
                    Layout.fillWidth: true
                    text: qsTr("Settings")
                    color: Theme.text
                    font.pixelSize: 18
                    font.weight: Font.DemiBold
                }
                IconButton {
                    iconName: "close"
                    glyphSize: 12
                    Accessible.name: qsTr("Close settings")
                    onClicked: root.close()
                }
            }

            SectionTitle { text: qsTr("Appearance") }
            RowLayout {
                spacing: 6
                Repeater {
                    model: [
                        { key: "system", label: qsTr("System") },
                        { key: "light", label: qsTr("Light") },
                        { key: "dark", label: qsTr("Dark") },
                    ]
                    PillButton {
                        required property var modelData
                        text: modelData.label
                        filled: shell !== null && shell.theme === modelData.key
                        onClicked: shell.setTheme(modelData.key)
                    }
                }
            }

            SectionTitle { text: qsTr("Notifications") }
            RowLayout {
                Layout.fillWidth: true
                spacing: 12
                ColumnLayout {
                    Layout.fillWidth: true
                    spacing: 2
                    Text {
                        text: qsTr("New messages")
                        color: Theme.text
                        font.pixelSize: Theme.fontBody
                        font.weight: Font.DemiBold
                    }
                    Text {
                        Layout.fillWidth: true
                        wrapMode: Text.Wrap
                        color: Theme.textMuted
                        font.pixelSize: Theme.fontSmall
                        text: qsTr("System notifications follow each chat's mute settings. "
                                   + "Mentions and replies in muted chats still notify.")
                    }
                }
                ToggleSwitch {
                    objectName: "notificationsSwitch"
                    Layout.alignment: Qt.AlignTop
                    checked: notifications.enabled
                    onToggled: notifications.setEnabled(checked)
                }
            }
            RowLayout {
                Layout.fillWidth: true
                spacing: 12
                Text {
                    Layout.fillWidth: true
                    wrapMode: Text.Wrap
                    text: qsTr("Show sender and message text")
                    color: notifications.enabled ? Theme.text : Theme.textMuted
                    font.pixelSize: Theme.fontBody
                }
                ToggleSwitch {
                    enabled: notifications.enabled
                    checked: notifications.showPreview
                    onToggled: notifications.setShowPreview(checked)
                }
            }
            RowLayout {
                Layout.fillWidth: true
                spacing: 12
                Text {
                    Layout.fillWidth: true
                    wrapMode: Text.Wrap
                    text: qsTr("Play a sound")
                    color: notifications.enabled ? Theme.text : Theme.textMuted
                    font.pixelSize: Theme.fontBody
                }
                ToggleSwitch {
                    objectName: "soundSwitch"
                    enabled: notifications.enabled
                    checked: notifications.sound
                    onToggled: notifications.setSound(checked)
                }
            }
            PillButton {
                text: qsTr("Send a test notification")
                iconName: "bell"
                enabled: notifications.enabled
                onClicked: notifications.sendTest()
            }

            SectionTitle {
                objectName: "securitySection"
                visible: lock.available
                text: qsTr("Passcode")
            }
            Paragraph {
                visible: lock.available
                color: Theme.textMuted
                font.pixelSize: Theme.fontSmall
                text: lock.hasPasscode
                      ? qsTr("The local database, AI results and the search index are encrypted "
                             + "with keys only your passcode opens. Without it the app shows "
                             + "nothing, and notifications have no text while locked.")
                      : qsTr("The local database is encrypted with a key kept in the system "
                             + "keychain. A passcode also locks the app and encrypts AI results "
                             + "and the search index; forgetting it means logging in again.")
            }
            ColumnLayout {
                id: passcodeForm
                objectName: "passcodeForm"
                property bool editing: false
                property string error: ""
                visible: lock.available
                Layout.fillWidth: true
                spacing: 8

                function reset() {
                    editing = false
                    error = ""
                    currentField.text = ""
                    newField.text = ""
                    repeatField.text = ""
                }

                component SecretField: TextField {
                    Layout.fillWidth: true
                    echoMode: TextInput.Password
                    color: Theme.text
                    placeholderTextColor: Theme.textMuted
                    font.pixelSize: Theme.fontBody
                    padding: 8
                    background: Rectangle {
                        radius: 8
                        color: Theme.field
                        border.width: 1
                        border.color: parent.activeFocus ? Theme.accent : Theme.fieldBorder
                    }
                }

                SecretField {
                    id: currentField
                    objectName: "currentPasscode"
                    visible: lock.hasPasscode && passcodeForm.editing
                    placeholderText: qsTr("Current passcode")
                }
                SecretField {
                    id: newField
                    objectName: "newPasscode"
                    visible: passcodeForm.editing
                    placeholderText: qsTr("New passcode")
                }
                SecretField {
                    id: repeatField
                    objectName: "repeatPasscode"
                    visible: passcodeForm.editing
                    placeholderText: qsTr("Repeat the new passcode")
                }
                Text {
                    visible: passcodeForm.error !== ""
                    text: passcodeForm.error
                    color: Theme.danger
                    font.pixelSize: Theme.fontSmall
                }
                RowLayout {
                    spacing: 6
                    PillButton {
                        visible: !passcodeForm.editing
                        text: lock.hasPasscode ? qsTr("Change passcode") : qsTr("Set a passcode")
                        iconName: "pin"
                        onClicked: passcodeForm.editing = true
                    }
                    PillButton {
                        objectName: "savePasscode"
                        visible: passcodeForm.editing
                        filled: true
                        text: qsTr("Save")
                        onClicked: {
                            if (newField.text !== repeatField.text) {
                                passcodeForm.error = qsTr("The passcodes don't match")
                                return
                            }
                            const error = lock.setPasscode(newField.text, currentField.text)
                            if (error === "")
                                passcodeForm.reset()
                            else
                                passcodeForm.error = qsTr(error)
                        }
                    }
                    PillButton {
                        visible: passcodeForm.editing && lock.hasPasscode
                        text: qsTr("Turn off")
                        danger: true
                        onClicked: {
                            const error = lock.removePasscode(currentField.text)
                            if (error === "")
                                passcodeForm.reset()
                            else
                                passcodeForm.error = qsTr(error)
                        }
                    }
                    PillButton {
                        visible: passcodeForm.editing
                        text: qsTr("Cancel")
                        onClicked: passcodeForm.reset()
                    }
                    PillButton {
                        visible: !passcodeForm.editing && lock.hasPasscode
                        text: Qt.platform.os === "osx" ? qsTr("Lock now (\u2318L)")
                                                       : qsTr("Lock now (Ctrl+L)")
                        onClicked: {
                            root.close()
                            lock.lockNow()
                        }
                    }
                }
                RowLayout {
                    visible: lock.hasPasscode && !passcodeForm.editing
                    spacing: 6
                    Text {
                        text: qsTr("Lock after")
                        color: Theme.text
                        font.pixelSize: Theme.fontBody
                    }
                    Repeater {
                        model: lock.autoLockChoices
                        PillButton {
                            required property int modelData
                            text: modelData === 0 ? qsTr("Never")
                                  : modelData === 60 ? qsTr("1 h") : qsTr("%1 min").arg(modelData)
                            filled: lock.autoLockMinutes === modelData
                            onClicked: lock.setAutoLock(modelData)
                        }
                    }
                }
            }

            SectionTitle { text: qsTr("Updates") }
            RowLayout {
                objectName: "updatesRow"
                Layout.fillWidth: true
                spacing: 12
                ColumnLayout {
                    Layout.fillWidth: true
                    spacing: 2
                    Text {
                        text: qsTr("Version %1").arg(updates.version)
                        color: Theme.text
                        font.pixelSize: Theme.fontBody
                        font.weight: Font.DemiBold
                    }
                    Text {
                        Layout.fillWidth: true
                        wrapMode: Text.Wrap
                        color: updates.state === "error" ? Theme.danger
                             : updates.state === "available" ? Theme.accent : Theme.textMuted
                        font.pixelSize: Theme.fontSmall
                        text: !updates.supported
                              ? qsTr("This build doesn't check for updates.")
                              : updates.state === "checking" ? qsTr("Checking\u2026")
                              : updates.state === "upToDate" ? qsTr("You have the latest version.")
                              : updates.state === "available"
                                ? qsTr("Version %1 is available.").arg(updates.newVersion)
                              : updates.state === "downloading"
                                ? qsTr("Downloading\u2026 %1%").arg(Math.round(updates.progress * 100))
                              : updates.state === "ready" ? qsTr("Downloaded. Restart to finish, "
                                                               + "or install the opened disk image.")
                              : updates.state === "error" ? updates.error
                              : qsTr("Checks GitHub once a day: one anonymous request, "
                                     + "nothing about you is sent.")
                    }
                }
                ToggleSwitch {
                    Layout.alignment: Qt.AlignTop
                    visible: updates.supported
                    checked: updates.automatic
                    onToggled: updates.setAutomatic(checked)
                }
            }
            RowLayout {
                visible: updates.supported
                spacing: 6
                PillButton {
                    text: qsTr("Check now")
                    enabled: updates.state !== "checking" && updates.state !== "downloading"
                    onClicked: updates.check()
                }
                PillButton {
                    visible: updates.state === "available"
                    text: updates.canInstall ? qsTr("Install %1").arg(updates.newVersion)
                                             : qsTr("Release page")
                    iconName: "update"
                    filled: true
                    onClicked: updates.install()
                }
                PillButton {
                    visible: updates.state === "ready"
                    text: qsTr("Restart")
                    filled: true
                    onClicked: updates.restart()
                }
            }

            SectionTitle { text: qsTr("Search") }
            Paragraph {
                color: Theme.textMuted
                text: (search.indexedMessages === 1 ? qsTr("Local index: 1 message")
                       : qsTr("Local index: %1 messages").arg(search.indexedMessages))
                      + " \u00b7 "
                      + (search.indexedChats === 1 ? qsTr("1 chat")
                         : qsTr("%1 chats").arg(search.indexedChats))
                      + (search.indexing ? " \u00b7 " + qsTr("indexing recent chats\u2026") : "")
            }
            RowLayout {
                Layout.fillWidth: true
                spacing: 12
                ColumnLayout {
                    Layout.fillWidth: true
                    spacing: 2
                    Text {
                        text: qsTr("Search by meaning")
                        color: Theme.text
                        font.pixelSize: Theme.fontBody
                        font.weight: Font.DemiBold
                    }
                    Text {
                        Layout.fillWidth: true
                        wrapMode: Text.Wrap
                        color: Theme.textMuted
                        font.pixelSize: Theme.fontSmall
                        text: search.semanticSupported
                              ? qsTr("Finds messages by what they mean, across languages, not only "
                                     + "by exact words. Downloads the model %1 (about 220 MB) once; "
                                     + "it runs on this computer and nothing is sent anywhere.")
                                .arg(search.embeddingModel)
                              : qsTr("Not installed. Run: pip install -e \".[semantic]\"")
                    }
                    Text {
                        Layout.fillWidth: true
                        visible: search.semanticEnabled && text !== ""
                        wrapMode: Text.Wrap
                        color: search.modelState === "error" ? Theme.danger : Theme.accent
                        font.pixelSize: Theme.fontSmall
                        text: search.modelState === "loading"
                              ? qsTr("Preparing the model (the first time it downloads)\u2026")
                              : search.modelState === "error" ? search.modelError
                              : search.modelState === "ready"
                                ? qsTr("%1 of %2 messages processed").arg(search.embeddedMessages)
                                  .arg(search.indexedMessages)
                              : ""
                    }
                }
                ToggleSwitch {
                    objectName: "semanticSwitch"
                    Layout.alignment: Qt.AlignTop
                    enabled: search.semanticSupported
                    checked: search.semanticEnabled
                    onToggled: search.setSemanticEnabled(checked)
                }
            }
            Bullet {
                text: qsTr("The index lives on this computer and keeps only words and vectors, "
                           + "not message text. Secret chats are never indexed. Results also "
                           + "include Telegram's own server search, like in any Telegram app.")
            }

            SectionTitle { text: qsTr("AI") }
            // OpenRouter key: shown masked; entering a new one checks it with OpenRouter first.
            ColumnLayout {
                id: keyBox
                objectName: "apiKeyBox"
                property bool editing: !ai.configured
                Layout.fillWidth: true
                spacing: 6

                Connections {
                    target: ai
                    function onKeySaved() {
                        keyField.text = ""
                        keyBox.editing = false
                    }
                    function onConfiguredChanged() {
                        if (!ai.configured)
                            keyBox.editing = true
                    }
                }

                RowLayout {
                    Layout.fillWidth: true
                    visible: ai.configured && !keyBox.editing
                    spacing: 8

                    ColumnLayout {
                        Layout.fillWidth: true
                        spacing: 1
                        Text {
                            text: qsTr("OpenRouter API key")
                            color: Theme.text
                            font.pixelSize: Theme.fontBody
                            font.weight: Font.DemiBold
                        }
                        Text {
                            Layout.fillWidth: true
                            wrapMode: Text.Wrap
                            textFormat: Text.PlainText
                            color: Theme.textMuted
                            font.pixelSize: Theme.fontSmall
                            text: ai.apiKeyHint === "" ? qsTr("Key is set")
                                  : ai.apiKeySource === "environment"
                                  ? qsTr("%1 \u00b7 from OPENROUTER_API_KEY in the environment")
                                    .arg(ai.apiKeyHint)
                                  : qsTr("%1 \u00b7 saved on this computer").arg(ai.apiKeyHint)
                        }
                    }
                    PillButton {
                        text: qsTr("Change")
                        onClicked: {
                            keyBox.editing = true
                            keyField.forceActiveFocus()
                        }
                    }
                    PillButton {
                        text: qsTr("Remove")
                        onClicked: ai.removeApiKey()
                    }
                }

                ColumnLayout {
                    Layout.fillWidth: true
                    visible: keyBox.editing
                    spacing: 6

                    Text {
                        text: qsTr("OpenRouter API key")
                        color: Theme.text
                        font.pixelSize: Theme.fontBody
                        font.weight: Font.DemiBold
                    }
                    RowLayout {
                        Layout.fillWidth: true
                        spacing: 8

                        TextField {
                            id: keyField
                            objectName: "apiKeyField"
                            Layout.fillWidth: true
                            placeholderText: "sk-or-v1-\u2026"
                            echoMode: TextInput.Password
                            enabled: !ai.keyBusy
                            color: Theme.text
                            placeholderTextColor: Theme.textMuted
                            font.pixelSize: Theme.fontBody
                            selectByMouse: true
                            background: Rectangle {
                                implicitHeight: 32
                                radius: 8
                                color: Theme.field
                                border.width: 1
                                border.color: keyField.activeFocus ? Theme.accent : Theme.fieldBorder
                            }
                            onAccepted: ai.saveApiKey(text)
                        }
                        PillButton {
                            text: ai.keyBusy ? qsTr("Checking\u2026") : qsTr("Save")
                            filled: true
                            enabled: !ai.keyBusy && keyField.text.trim() !== ""
                            onClicked: ai.saveApiKey(keyField.text)
                        }
                        PillButton {
                            visible: ai.configured
                            text: qsTr("Cancel")
                            onClicked: {
                                keyField.text = ""
                                keyBox.editing = false
                            }
                        }
                    }
                    Text {
                        Layout.fillWidth: true
                        visible: ai.keyError !== ""
                        wrapMode: Text.Wrap
                        textFormat: Text.PlainText
                        text: ai.keyError
                        color: Theme.danger
                        font.pixelSize: Theme.fontSmall
                    }
                    Flow {
                        Layout.fillWidth: true
                        spacing: 4
                        Text {
                            text: qsTr("Create a key at")
                            color: Theme.textMuted
                            font.pixelSize: Theme.fontSmall
                        }
                        Text {
                            text: "openrouter.ai/keys"
                            color: Theme.link
                            font.pixelSize: Theme.fontSmall
                            HoverHandler { cursorShape: Qt.PointingHandCursor }
                            TapHandler { onTapped: Qt.openUrlExternally("https://openrouter.ai/keys") }
                        }
                    }
                    Text {
                        Layout.fillWidth: true
                        wrapMode: Text.Wrap
                        text: qsTr("The key is checked with OpenRouter (free) and stored on this "
                                   + "computer, readable only by your user. It is never included "
                                   + "in the app or sent anywhere else.")
                        color: Theme.textMuted
                        font.pixelSize: Theme.fontSmall
                    }
                }
            }
            // Models: the main one for summaries and answers, a cheap one for many small
            // requests (translation, smart notifications), one that understands audio.
            GridLayout {
                id: modelGrid
                objectName: "modelGrid"
                Layout.fillWidth: true
                columns: 2
                columnSpacing: 10
                rowSpacing: 6

                component ModelField: TextField {
                    Layout.fillWidth: true
                    color: Theme.text
                    font.pixelSize: Theme.fontSmall
                    selectByMouse: true
                    background: Rectangle {
                        implicitHeight: 28
                        radius: 7
                        color: Theme.field
                        border.width: 1
                        border.color: parent.activeFocus ? Theme.accent : Theme.fieldBorder
                    }
                }
                component FieldLabel: Text {
                    color: Theme.textMuted
                    font.pixelSize: Theme.fontSmall
                }

                FieldLabel { text: qsTr("Summaries and answers") }
                ModelField { id: summaryModelField; text: ai.summaryModel }
                FieldLabel { text: qsTr("Translation, quick checks") }
                ModelField { id: cheapModelField; text: ai.cheapModel }
                FieldLabel { text: qsTr("Voice transcription") }
                ModelField { id: transcriptionModelField; text: ai.transcriptionModel }
            }
            RowLayout {
                Layout.fillWidth: true
                spacing: 8
                Text {
                    Layout.fillWidth: true
                    wrapMode: Text.Wrap
                    text: qsTr("OpenRouter model ids. Each needs a zero-data-retention provider.")
                    color: Theme.textMuted
                    font.pixelSize: Theme.fontSmall
                }
                PillButton {
                    text: qsTr("Save models")
                    enabled: summaryModelField.text !== ai.summaryModel
                             || cheapModelField.text !== ai.cheapModel
                             || transcriptionModelField.text !== ai.transcriptionModel
                    onClicked: ai.setModels(summaryModelField.text, cheapModelField.text,
                                            transcriptionModelField.text)
                }
            }

            ColumnLayout {
                Layout.fillWidth: true
                spacing: 6
                ColumnLayout {
                    Layout.fillWidth: true
                    spacing: 1
                    Text {
                        text: qsTr("Language of AI answers")
                        color: Theme.text
                        font.pixelSize: Theme.fontBody
                        font.weight: Font.DemiBold
                    }
                    Text {
                        Layout.fillWidth: true
                        wrapMode: Text.Wrap
                        text: qsTr("Summaries, explanations, answers and translations come in "
                                   + "this language. Suggested replies stay in the chat's "
                                   + "language, with a translation.")
                        color: Theme.textMuted
                        font.pixelSize: Theme.fontSmall
                    }
                }
                Row {
                    spacing: 6
                    Repeater {
                        objectName: "aiLanguages"
                        model: ai.languages
                        PillButton {
                            required property var modelData
                            text: modelData.label
                            filled: ai.translateTo === modelData.code
                            onClicked: ai.setTranslateTo(modelData.code)
                        }
                    }
                }
                Text {
                    Layout.fillWidth: true
                    Layout.topMargin: 4
                    wrapMode: Text.Wrap
                    text: qsTr("I also read: suggested replies in these languages come "
                               + "without a translation.")
                    color: Theme.textMuted
                    font.pixelSize: Theme.fontSmall
                }
                Row {
                    spacing: 6
                    Repeater {
                        objectName: "readLanguages"
                        model: ai.languages
                        PillButton {
                            required property var modelData
                            readonly property bool own: ai.translateTo === modelData.code
                            text: modelData.label
                            filled: ai.readLanguages.indexOf(modelData.code) >= 0
                            enabled: !own
                            onClicked: ai.setReads(modelData.code, !filled)
                        }
                    }
                }
            }

            RowLayout {
                Layout.fillWidth: true
                spacing: 8
                ColumnLayout {
                    Layout.fillWidth: true
                    spacing: 1
                    Text {
                        text: qsTr("Monthly limit per chat")
                        color: Theme.text
                        font.pixelSize: Theme.fontBody
                    }
                    Text {
                        Layout.fillWidth: true
                        wrapMode: Text.Wrap
                        text: qsTr("In US dollars, 0 for no limit. Digests and promises count "
                                   + "as one more \u201cchat\u201d. Spent this month: %1")
                              .arg(ai.spentTotal)
                        color: Theme.textMuted
                        font.pixelSize: Theme.fontSmall
                    }
                }
                TextField {
                    id: limitField
                    objectName: "limitField"
                    Layout.preferredWidth: 70
                    text: ai.monthlyLimit.toFixed(2)
                    horizontalAlignment: TextInput.AlignRight
                    validator: DoubleValidator { bottom: 0; top: 1000; decimals: 2 }
                    color: Theme.text
                    font.pixelSize: Theme.fontBody
                    selectByMouse: true
                    background: Rectangle {
                        implicitHeight: 28
                        radius: 7
                        color: Theme.field
                        border.width: 1
                        border.color: limitField.activeFocus ? Theme.accent : Theme.fieldBorder
                    }
                    onEditingFinished: ai.setMonthlyLimit(Number(text.replace(",", ".")) || 0)
                }
            }
            Repeater {
                model: ai.spending
                RowLayout {
                    required property var modelData
                    Layout.fillWidth: true
                    Layout.leftMargin: 12
                    Text {
                        Layout.fillWidth: true
                        text: modelData.title
                        textFormat: Text.PlainText
                        elide: Text.ElideRight
                        color: Theme.textMuted
                        font.pixelSize: Theme.fontSmall
                    }
                    Text {
                        text: modelData.amount
                        color: Theme.textMuted
                        font.pixelSize: Theme.fontSmall
                    }
                }
            }

            SectionTitle { text: qsTr("What is sent, and where") }
            Bullet {
                text: qsTr("Nothing leaves this computer until you turn AI on for a chat "
                           + "and press Summarize or Transcribe there. AI is off for every chat "
                           + "by default.")
            }
            Bullet {
                text: qsTr("Summarize sends the messages in the range you pick (text, captions, "
                           + "sender names, times, and existing transcripts) to OpenRouter, which "
                           + "passes them to the model's provider.")
            }
            Bullet {
                text: qsTr("Transcribe sends the audio of that one voice message the same way.")
            }
            Bullet {
                text: qsTr("Translate sends that one message; translating your own text sends "
                           + "what you typed, and nothing is sent to the chat until you press "
                           + "Send. Suggest a reply sends the last 30 messages of the chat.")
            }
            Bullet {
                text: qsTr("Asking about a chat sends the question and the messages that search "
                           + "found for it plus the latest ones. Dates and meetings sends the "
                           + "last 30 days; Collect answers sends the messages after the question "
                           + "and the names of those it was addressed to. Asking about a file "
                           + "sends the whole file.")
            }
            Bullet {
                text: qsTr("The digest is made when you press it, from chats you added to it, "
                           + "since the previous digest. \u201cWhat did I promise\u201d looks "
                           + "through the last 14 days of every chat with AI on.")
            }
            Bullet {
                text: qsTr("Explain and Suggest reply send that message, the messages it replies "
                           + "to, up to 20 messages before and after it, and the last summary of "
                           + "the chat if there is one; Suggest reply also sends up to 15 of your "
                           + "own messages from that chat so the drafts sound like you. The menu "
                           + "shows how many messages go before you click.")
            }
            Bullet {
                text: qsTr("Smart notifications are the only automatic feature: in chats where "
                           + "you turned them on, each new message that would notify you is sent "
                           + "with a few earlier ones to %1 to decide whether to show it.")
                      .arg(ai.cheapModel)
            }
            Bullet {
                text: qsTr("Every request asks OpenRouter to use only providers with zero data "
                           + "retention that don't train on your data. If the model has no such "
                           + "provider, the request fails instead of falling back.")
            }
            Bullet {
                text: qsTr("Secret chats are never sent, and AI can't be turned on for them.")
            }
            Bullet {
                text: qsTr("Transcripts and summaries are stored only on this computer.")
            }

            SectionTitle { text: qsTr("Chats with AI turned on") }
            Paragraph {
                visible: ai.enabledChats.length === 0
                color: Theme.textMuted
                text: qsTr("None.")
            }
            Repeater {
                model: ai.enabledChats
                RowLayout {
                    required property var modelData
                    Layout.fillWidth: true
                    ColumnLayout {
                        Layout.fillWidth: true
                        spacing: 0
                        Text {
                            Layout.fillWidth: true
                            text: modelData.title
                            textFormat: Text.PlainText
                            elide: Text.ElideRight
                            color: Theme.text
                            font.pixelSize: Theme.fontBody
                        }
                        Text {
                            Layout.fillWidth: true
                            text: [modelData.digest ? qsTr("in the digest") : "",
                                   modelData.smart ? qsTr("smart notifications") : "",
                                   qsTr("%1 this month").arg(modelData.spent)]
                                  .filter(t => t !== "").join(" \u00b7 ")
                            elide: Text.ElideRight
                            color: Theme.textMuted
                            font.pixelSize: Theme.fontSmall
                        }
                    }
                    PillButton {
                        text: qsTr("Turn off")
                        onClicked: ai.setChatEnabled(modelData.chatId, false)
                    }
                    PillButton {
                        text: qsTr("Delete AI data")
                        ToolTip.visible: hovered
                        ToolTip.delay: 600
                        ToolTip.text: qsTr("Turn AI off and delete this chat's transcripts, "
                                           + "translations and summaries")
                        onClicked: ai.forgetChat(modelData.chatId)
                    }
                }
            }
        }
    }
}
