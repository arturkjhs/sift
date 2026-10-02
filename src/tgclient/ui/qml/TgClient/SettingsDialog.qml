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

            SectionTitle { text: qsTr("AI: summaries and voice transcription") }
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
            Paragraph {
                textFormat: Text.PlainText
                color: Theme.textMuted
                text: qsTr("Summaries: %1\nTranscription: %2")
                      .arg(ai.summaryModel).arg(ai.transcriptionModel)
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
                    Text {
                        Layout.fillWidth: true
                        text: modelData.title
                        textFormat: Text.PlainText
                        elide: Text.ElideRight
                        color: Theme.text
                        font.pixelSize: Theme.fontBody
                    }
                    PillButton {
                        text: qsTr("Turn off")
                        onClicked: ai.setChatEnabled(modelData.chatId, false)
                    }
                }
            }
        }
    }
}
