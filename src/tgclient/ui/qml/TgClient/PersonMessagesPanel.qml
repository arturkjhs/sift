import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts

// One person's messages in the open chat or in all chats in common with them: find and check
// what exactly they wrote. A click jumps to the message (opening its chat). "Check" asks AI
// whether they said something; quotes come from the messages themselves, not from the model.
Rectangle {
    id: root
    objectName: "personMessagesPanel"
    color: Theme.sidebar

    signal closeRequested()
    signal messageRequested(var chatId, var messageId)

    property bool checking: false  // the "Check" tab

    function focusSearch() { searchBox.focusInput() }
    function startCheck() {
        root.checking = true
        claimField.forceActiveFocus()
    }

    readonly property var verdictLabels: ({
        confirmed: qsTr("Confirmed"),
        contradicted: qsTr("Contradicted"),
        partly: qsTr("Partly"),
        not_found: qsTr("Not found"),
    })

    Binding {
        target: personMessages
        property: "highlightColor"
        value: Qt.rgba(Theme.accent.r, Theme.accent.g, Theme.accent.b, 0.3).toString()
    }

    ColumnLayout {
        anchors.fill: parent
        anchors.margins: 14
        spacing: 8

        RowLayout {
            Layout.fillWidth: true
            Text {
                Layout.fillWidth: true
                text: root.checking ? qsTr("Check what %1 said").arg(personMessages.senderName)
                      : personMessages.onBehalf
                      ? qsTr("Messages on behalf of %1").arg(personMessages.senderName)
                      : qsTr("Messages from %1").arg(personMessages.senderName)
                textFormat: Text.PlainText
                elide: Text.ElideRight
                color: Theme.text
                font.pixelSize: Theme.fontTitle
                font.weight: Font.DemiBold
            }
            IconButton {
                iconName: "close"
                glyphSize: 12
                Accessible.name: qsTr("Close")
                onClicked: root.closeRequested()
            }
        }

        Row {
            spacing: 4
            visible: personMessages.sender !== ""
            PillButton {
                objectName: "personMessagesTab"
                text: qsTr("Messages")
                filled: !root.checking
                onClicked: root.checking = false
            }
            PillButton {
                objectName: "claimCheckTab"
                text: qsTr("Check")
                iconName: "sparkle"
                filled: root.checking
                onClicked: root.startCheck()
            }
        }

        SearchBox {
            id: searchBox
            objectName: "personSearchBox"
            visible: !root.checking
            Layout.fillWidth: true
            placeholder: qsTr("Search their messages")
            onEdited: text => personMessages.query = text
        }

        RowLayout {
            Layout.fillWidth: true
            visible: personMessages.canSearchAllChats
            spacing: 8
            Text {
                Layout.fillWidth: true
                text: qsTr("In all common chats")
                color: Theme.text
                font.pixelSize: Theme.fontBody
            }
            ToggleSwitch {
                objectName: "allChatsSwitch"
                checked: personMessages.allChats
                onToggled: personMessages.allChats = checked
            }
        }

        Text {
            objectName: "personCoverage"
            Layout.fillWidth: true
            visible: !root.checking && personMessages.allChats && personMessages.chatsTotal > 0
            text: personMessages.chatsSearched < personMessages.chatsTotal
                  ? qsTr("Chats searched: %1 of %2").arg(personMessages.chatsSearched)
                                                    .arg(personMessages.chatsTotal)
                  : qsTr("Chats searched: %1").arg(personMessages.chatsSearched)
            color: Theme.textMuted
            font.pixelSize: Theme.fontSmall
        }

        // "Check what <name> said…"
        ColumnLayout {
            Layout.fillWidth: true
            visible: root.checking
            spacing: 6

            TextField {
                id: claimField
                objectName: "claimField"
                Layout.fillWidth: true
                placeholderText: qsTr("What did %1 say?").arg(personMessages.senderName)
                enabled: personMessages.checkState !== "pending"
                color: Theme.text
                placeholderTextColor: Theme.textMuted
                font.pixelSize: Theme.fontBody
                selectByMouse: true
                background: Rectangle {
                    implicitHeight: 32
                    radius: 8
                    color: Theme.field
                    border.width: 1
                    border.color: claimField.activeFocus ? Theme.accent : Theme.fieldBorder
                }
                onAccepted: if (text.trim() !== "") personMessages.check(text)
                Keys.onEscapePressed: root.checking = false
            }
            RowLayout {
                Layout.fillWidth: true
                Text {
                    Layout.fillWidth: true
                    text: personMessages.allChats ? qsTr("Only chats with AI on are checked")
                                                  : qsTr("Checked in this chat (AI must be on)")
                    wrapMode: Text.Wrap
                    color: Theme.textMuted
                    font.pixelSize: Theme.fontSmall
                }
                PillButton {
                    objectName: "claimCheckButton"
                    text: qsTr("Check")
                    filled: true
                    enabled: claimField.enabled && claimField.text.trim() !== ""
                    onClicked: claimField.accepted()
                }
            }
        }

        Flickable {
            id: checkResult
            objectName: "claimResult"
            Layout.fillWidth: true
            Layout.fillHeight: true
            visible: root.checking
            clip: true
            contentHeight: result.implicitHeight
            boundsBehavior: Flickable.StopAtBounds
            ScrollBar.vertical: ScrollBar {}

            ColumnLayout {
                id: result
                width: checkResult.width
                spacing: 8

                Text {
                    visible: personMessages.checkState === "pending"
                    text: qsTr("Checking…")
                    color: Theme.textMuted
                    font.pixelSize: Theme.fontSmall
                }
                Text {
                    Layout.fillWidth: true
                    visible: personMessages.checkState === "error"
                    text: personMessages.checkError
                    wrapMode: Text.Wrap
                    color: Theme.danger
                    font.pixelSize: Theme.fontSmall
                }

                Rectangle {  // the verdict
                    objectName: "verdictBadge"
                    visible: personMessages.checkState === "done"
                    implicitWidth: verdictText.implicitWidth + 20
                    implicitHeight: 26
                    radius: 13
                    color: personMessages.verdict === "confirmed" ? Theme.accent
                         : personMessages.verdict === "contradicted" ? Theme.danger
                         : personMessages.verdict === "partly" ? Theme.badgeMuted
                         : Theme.pill
                    Text {
                        id: verdictText
                        anchors.centerIn: parent
                        text: root.verdictLabels[personMessages.verdict] || ""
                        color: personMessages.verdict === "not_found" ? Theme.text
                                                                      : Theme.textOnAccent
                        font.pixelSize: Theme.fontSmall
                        font.weight: Font.DemiBold
                    }
                }
                Text {
                    Layout.fillWidth: true
                    visible: personMessages.checkState === "done" && text !== ""
                    text: personMessages.explanation
                    textFormat: Text.PlainText
                    wrapMode: Text.Wrap
                    color: Theme.text
                    font.pixelSize: Theme.fontBody
                }
                // not_found is not proof: say exactly what was looked at
                Text {
                    objectName: "notFoundScope"
                    Layout.fillWidth: true
                    visible: personMessages.checkState === "done"
                             && personMessages.verdict === "not_found"
                    text: (personMessages.checkedCount > 0
                           ? qsTr("Not found in %1 messages (%2) in %3.")
                                 .arg(personMessages.checkedCount).arg(personMessages.checkedPeriod)
                                 .arg(personMessages.checkedChats)
                           : qsTr("No messages from %1 to check in %2.")
                                 .arg(personMessages.senderName).arg(personMessages.checkedChats))
                          + " " + qsTr("This doesn't prove it was never said.")
                    wrapMode: Text.Wrap
                    color: Theme.textMuted
                    font.pixelSize: Theme.fontSmall
                }

                Repeater {
                    objectName: "claimQuotes"
                    model: personMessages.quotes
                    delegate: Rectangle {
                        id: quote
                        required property var modelData
                        Layout.fillWidth: true
                        implicitHeight: quoteColumn.implicitHeight + 16
                        radius: 8
                        color: quoteHover.hovered ? Theme.hover : Theme.window
                        Rectangle {  // supports / contradicts
                            width: 3
                            height: parent.height - 12
                            anchors.verticalCenter: parent.verticalCenter
                            x: 4
                            radius: 1.5
                            color: quote.modelData.relation === "contradicts" ? Theme.danger
                                                                               : Theme.accent
                        }
                        HoverHandler { id: quoteHover; cursorShape: Qt.PointingHandCursor }
                        TapHandler {
                            onTapped: root.messageRequested(quote.modelData.chatId,
                                                            quote.modelData.messageId)
                        }
                        Column {
                            id: quoteColumn
                            x: 14
                            y: 8
                            width: parent.width - 24
                            spacing: 3
                            Text {
                                width: parent.width
                                text: quote.modelData.time + "  ·  " + quote.modelData.chatTitle
                                      + "  ·  " + (quote.modelData.relation === "contradicts"
                                                   ? qsTr("contradicts") : qsTr("supports"))
                                textFormat: Text.PlainText
                                elide: Text.ElideRight
                                color: Theme.textMuted
                                font.pixelSize: Theme.fontSmall
                            }
                            Text {
                                width: parent.width
                                text: quote.modelData.text
                                textFormat: Text.RichText
                                wrapMode: Text.Wrap
                                color: Theme.text
                                font.pixelSize: Theme.fontBody
                            }
                        }
                    }
                }

                Text {
                    objectName: "skippedChats"
                    Layout.fillWidth: true
                    visible: personMessages.skippedChats > 0
                             && personMessages.checkState !== ""
                    text: qsTr("Not checked (AI is off there): %1 chats")
                              .arg(personMessages.skippedChats)
                    wrapMode: Text.Wrap
                    color: Theme.textMuted
                    font.pixelSize: Theme.fontSmall
                }
                Text {
                    Layout.fillWidth: true
                    visible: personMessages.checkState === "done"
                             && personMessages.checkedCount > 0
                    text: [personMessages.checkCost, personMessages.checkModel]
                              .filter(s => s !== "").join("  ·  ")
                    color: Theme.textMuted
                    font.pixelSize: Theme.fontSmall
                }
            }
        }

        Text {
            Layout.fillWidth: true
            visible: !root.checking && text !== ""
            text: personMessages.busy && list.count === 0 ? qsTr("Searching…")
                  : list.count === 0 ? qsTr("Nothing found")
                  : ""
            color: Theme.textMuted
            font.pixelSize: Theme.fontSmall
        }

        ListView {
            id: list
            objectName: "personMessageList"
            visible: !root.checking
            Layout.fillWidth: true
            Layout.fillHeight: true
            clip: true
            model: personMessages
            spacing: 6
            boundsBehavior: Flickable.StopAtBounds
            ScrollBar.vertical: ScrollBar {}

            delegate: Item {
                id: row
                required property string kind
                required property bool more
                required property var chatId
                required property var messageId
                required property string chatTitle
                required property string topic
                required property string time
                required property string text
                required property string media

                width: ListView.view.width
                height: kind === "header" ? header.implicitHeight + 10 : card.height

                // "In all common chats": the chat's results start here
                RowLayout {
                    id: header
                    visible: row.kind === "header"
                    anchors.left: parent.left
                    anchors.right: parent.right
                    anchors.bottom: parent.bottom
                    anchors.leftMargin: 2
                    spacing: 6
                    Text {
                        Layout.fillWidth: true
                        text: row.chatTitle
                        textFormat: Text.PlainText
                        elide: Text.ElideRight
                        color: Theme.text
                        font.pixelSize: Theme.fontSmall
                        font.weight: Font.DemiBold
                    }
                    Text {
                        objectName: "chatMore"
                        visible: row.more
                        text: qsTr("More")
                        color: Theme.accent
                        font.pixelSize: Theme.fontSmall
                        HoverHandler { cursorShape: Qt.PointingHandCursor }
                        TapHandler { onTapped: personMessages.loadMoreIn(row.chatId) }
                    }
                }

                Rectangle {
                    id: card
                    visible: row.kind !== "header"
                    width: parent.width
                    height: visible ? column.implicitHeight + 16 : 0
                    radius: 8
                    color: hover.hovered ? Theme.hover : Theme.window

                    HoverHandler { id: hover; cursorShape: Qt.PointingHandCursor }
                    TapHandler { onTapped: root.messageRequested(row.chatId, row.messageId) }

                    Column {
                        id: column
                        x: 10
                        y: 8
                        width: parent.width - 20
                        spacing: 3
                        Row {
                            width: parent.width
                            spacing: 6
                            Text {
                                text: row.time
                                color: Theme.accent
                                font.pixelSize: Theme.fontSmall
                                font.weight: Font.DemiBold
                            }
                            Text {
                                width: parent.width - x
                                visible: text !== ""
                                text: row.topic !== "" ? "# " + row.topic : ""
                                textFormat: Text.PlainText
                                elide: Text.ElideRight
                                color: Theme.textMuted
                                font.pixelSize: Theme.fontSmall
                            }
                        }
                        Text {
                            visible: row.media !== ""
                            text: row.media
                            color: Theme.textMuted
                            font.pixelSize: Theme.fontSmall
                            font.italic: true
                        }
                        Text {
                            width: parent.width
                            visible: row.text !== ""
                            text: row.text
                            textFormat: Text.RichText
                            wrapMode: Text.Wrap
                            maximumLineCount: 8
                            elide: Text.ElideRight
                            color: Theme.text
                            font.pixelSize: Theme.fontBody
                        }
                    }
                }
            }

            footer: Item {
                width: ListView.view ? ListView.view.width : 0
                height: personMessages.busy && list.count > 0 ? 30 : 0
                Text {
                    anchors.centerIn: parent
                    visible: parent.height > 0
                    text: qsTr("Loading…")
                    color: Theme.textMuted
                    font.pixelSize: Theme.fontSmall
                }
            }
        }
    }
}
