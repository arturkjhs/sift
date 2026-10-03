import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts

// The AI panel of the open chat: summaries, a person, questions about the chat or a file,
// dates and meetings, answers to a question, one message explained. Citations are
// tgc://message/<id> links that scroll the feed. An explained light message (a joke, emoji,
// agreement) offers reactions and one short reply instead of an analysis.
Rectangle {
    id: root
    objectName: "summaryPanel"
    color: Theme.sidebar

    signal closeRequested()
    signal messageRequested(var messageId)
    signal reactionRequested(var messageId, string key)

    readonly property bool pending: ai.summaryState === "pending"
    readonly property bool streams: kind === "explain" || kind === "reply"
    readonly property bool hasOptions: kind === "reply" && ai.replyOptions.length > 0
    readonly property bool light: kind === "explain" && ai.explainKind === "light"
    property bool analysisShown: false
    readonly property string kind: ai.subject === "" ? "summary"
                                   : ai.subject.startsWith("user:") || ai.subject.startsWith("chat:")
                                     ? "person"
                                   : ai.subject.split(":")[0]   // ask | events | answers | doc

    function focusQuestion() { questionField.forceActiveFocus() }

    function title() {
        switch (root.kind) {
        case "person": return qsTr("About %1").arg(ai.summaryName)
        case "ask": return qsTr("Ask this chat")
        case "events": return qsTr("Dates and meetings")
        case "answers": return qsTr("Answers")
        case "doc": return qsTr("Ask about %1").arg(ai.summaryName || qsTr("the file"))
        case "explain": return ai.summaryName !== "" ? qsTr("Message from %1").arg(ai.summaryName)
                                                     : qsTr("About this message")
        case "reply": return qsTr("Reply options")
        }
        return qsTr("Summary")
    }

    function emptyText() {
        switch (root.kind) {
        case "ask": return qsTr("Ask anything about this chat: what was decided, who sent a "
                                + "link, when the meeting is. The answer links to the messages.")
        case "doc": return qsTr("Ask a question about this file. The whole file is sent "
                                + "with the question.")
        }
        return qsTr("Nothing yet.")
    }

    ColumnLayout {
        anchors.fill: parent
        anchors.margins: 14
        spacing: 6

        RowLayout {
            Layout.fillWidth: true
            spacing: 4

            Text {
                Layout.fillWidth: true
                text: root.title()
                textFormat: Text.PlainText
                elide: Text.ElideRight
                color: Theme.text
                font.pixelSize: Theme.fontTitle
                font.weight: Font.DemiBold
            }
            IconButton {
                iconName: "refresh"
                visible: !ai.canAsk || ai.summaryQuestion !== ""
                enabled: !root.pending && ai.enabled && ai.summaryScope !== ""
                Accessible.name: qsTr("Run again")
                onClicked: ai.summarizeAgain()
            }
            IconButton {
                iconName: "close"
                glyphSize: 12
                Accessible.name: qsTr("Close panel")
                onClicked: root.closeRequested()
            }
        }

        Text {  // the question being answered
            Layout.fillWidth: true
            visible: ai.canAsk && ai.summaryQuestion !== ""
            text: ai.summaryQuestion
            textFormat: Text.PlainText
            wrapMode: Text.Wrap
            maximumLineCount: 3
            elide: Text.ElideRight
            color: Theme.text
            font.pixelSize: Theme.fontBody
            font.italic: true
        }

        Text {
            Layout.fillWidth: true
            visible: text !== ""
            text: root.pending ? (root.kind === "summary" || root.kind === "person"
                                  ? qsTr("Summarizing…")
                                  : root.streams && ai.summaryHtml !== "" ? qsTr("Writing…")
                                  : qsTr("Thinking…"))
                               : ai.summaryInfo
            color: root.pending ? Theme.accent : Theme.textMuted
            font.pixelSize: Theme.fontSmall
            wrapMode: Text.Wrap
        }

        Text {
            Layout.fillWidth: true
            visible: ai.summaryState === "error"
            text: ai.summaryError
            textFormat: Text.PlainText
            wrapMode: Text.Wrap
            color: Theme.danger
            font.pixelSize: Theme.fontBody
        }

        Text {
            Layout.fillWidth: true
            visible: ai.summaryHtml === "" && ai.summaryState === ""
            text: root.emptyText()
            wrapMode: Text.Wrap
            color: Theme.textMuted
            font.pixelSize: Theme.fontBody
        }

        Flickable {
            id: flick
            Layout.fillWidth: true
            Layout.fillHeight: true
            clip: true
            contentWidth: width
            contentHeight: contentColumn.height
            boundsBehavior: Flickable.StopAtBounds
            // Streamed answers grow in place; older results being redone are dimmed.
            opacity: root.pending && !root.streams ? 0.5 : 1

            Column {
                id: contentColumn
                width: flick.width - 8
                spacing: 8

            Text {
                id: summaryText
                visible: !root.hasOptions
                width: parent.width
                text: ai.summaryHtml
                textFormat: Text.RichText
                wrapMode: Text.Wrap
                color: Theme.text
                font.pixelSize: Theme.fontBody
                onLinkActivated: link => {
                    const id = ai.messageIdFromLink(link)
                    if (id !== 0)
                        root.messageRequested(id)
                    else
                        messages.openLink(link)  // t.me message links stay in the app
                }

                // Citation chips: who wrote it and when.
                ToolTip.text: hoveredLink !== "" ? ai.linkTooltip(hoveredLink) : ""
                ToolTip.visible: ToolTip.text !== ""
                ToolTip.delay: 300

                HoverHandler {
                    cursorShape: summaryText.linkAt(point.position.x, point.position.y) !== ""
                                 ? Qt.PointingHandCursor : Qt.ArrowCursor
                }
            }

            // A light message: react, or answer in a few words.
            Flow {
                objectName: "quickReactions"
                visible: root.light && ai.quickReactions.length > 0
                width: parent.width
                spacing: 6
                Repeater {
                    model: root.light ? ai.quickReactions : []
                    Rectangle {
                        id: chip
                        objectName: "quickReaction"
                        required property var modelData
                        property bool sent: false
                        width: 46
                        height: 34
                        radius: 17
                        color: sent ? Theme.accent
                             : Qt.rgba(Theme.accent.r, Theme.accent.g, Theme.accent.b,
                                       chipHover.hovered ? 0.22 : 0.13)
                        Accessible.role: Accessible.Button
                        Accessible.name: qsTr("React with %1").arg(modelData.label)

                        Text {
                            anchors.centerIn: parent
                            text: chip.modelData.label
                            textFormat: Text.PlainText
                            font.pixelSize: 18
                        }
                        HoverHandler {
                            id: chipHover
                            cursorShape: Qt.PointingHandCursor
                        }
                        TapHandler {
                            onTapped: {
                                root.reactionRequested(ai.explainedMessageId, chip.modelData.key)
                                chip.sent = true
                            }
                        }
                    }
                }
            }
            Rectangle {
                objectName: "quickReply"
                visible: root.light && ai.quickReply !== ""
                width: contentColumn.width
                height: quickColumn.implicitHeight + 20
                radius: 10
                color: Theme.window
                border.width: 1
                border.color: Theme.separator

                Column {
                    id: quickColumn
                    x: 10
                    y: 10
                    width: parent.width - 20
                    spacing: 6

                    TextEdit {
                        width: parent.width
                        text: ai.quickReply
                        textFormat: TextEdit.PlainText
                        wrapMode: TextEdit.Wrap
                        readOnly: true
                        selectByMouse: true
                        color: Theme.text
                        selectionColor: Theme.accent
                        selectedTextColor: Theme.textOnAccent
                        font.pixelSize: Theme.fontBody
                    }
                    PillButton {
                        objectName: "insertQuickReply"
                        text: qsTr("Insert")
                        iconName: "edit"
                        enabled: !root.pending
                        onClicked: ai.insertQuickReply()
                    }
                }
            }

            // Reply options: the analysis folded away, then one card per strategy.
            Text {
                visible: root.hasOptions && ai.replyAnalysis !== ""
                text: (root.analysisShown ? "▾ " : "▸ ") + qsTr("Analysis")
                color: Theme.textMuted
                font.pixelSize: Theme.fontSmall
                font.weight: Font.DemiBold
                HoverHandler { cursorShape: Qt.PointingHandCursor }
                TapHandler { onTapped: root.analysisShown = !root.analysisShown }
            }
            Text {
                visible: root.hasOptions && root.analysisShown
                width: parent.width
                text: ai.replyAnalysis
                textFormat: Text.PlainText
                wrapMode: Text.Wrap
                color: Theme.text
                font.pixelSize: Theme.fontBody
                font.italic: true
            }
            Repeater {
                model: root.hasOptions ? ai.replyOptions : []
                Rectangle {
                    id: card
                    objectName: "replyOption"
                    required property var modelData
                    required property int index
                    width: contentColumn.width
                    height: cardColumn.implicitHeight + 20
                    radius: 10
                    color: Theme.window
                    border.width: 1
                    border.color: Theme.separator

                    Column {
                        id: cardColumn
                        x: 10
                        y: 10
                        width: parent.width - 20
                        spacing: 4

                        Text {
                            width: parent.width
                            text: card.modelData.label
                            textFormat: Text.PlainText
                            elide: Text.ElideRight
                            color: Theme.accent
                            font.pixelSize: Theme.fontSmall
                            font.weight: Font.DemiBold
                        }
                        TextEdit {
                            width: parent.width
                            text: card.modelData.text
                            textFormat: TextEdit.PlainText
                            wrapMode: TextEdit.Wrap
                            readOnly: true
                            selectByMouse: true
                            color: Theme.text
                            selectionColor: Theme.accent
                            selectedTextColor: Theme.textOnAccent
                            font.pixelSize: Theme.fontBody
                        }
                        Text {  // what it says, when the chat is in another language
                            visible: text !== ""
                            width: parent.width
                            text: card.modelData.translation
                            textFormat: Text.PlainText
                            wrapMode: Text.Wrap
                            color: Theme.textMuted
                            font.pixelSize: Theme.fontSmall
                            font.italic: true
                        }
                        PillButton {
                            objectName: "insertOption"
                            text: qsTr("Insert")
                            iconName: "edit"
                            enabled: !root.pending
                            onClicked: ai.insertOption(card.index)
                        }
                    }
                }
            }
            }

            ScrollBar.vertical: ScrollBar {}
        }

        Flow {  // refine the replies
            Layout.fillWidth: true
            visible: root.hasOptions
            spacing: 6
            PillButton {
                text: qsTr("Another option")
                enabled: !root.pending
                onClicked: ai.refineReplies("another")
            }
            PillButton {
                text: qsTr("Shorter")
                enabled: !root.pending
                onClicked: ai.refineReplies("shorter")
            }
            PillButton {
                text: qsTr("More formal")
                enabled: !root.pending
                onClicked: ai.refineReplies("formal")
            }
        }

        PillButton {  // something is asked of the user: offer full reply options
            objectName: "suggestFromExplain"
            visible: root.kind === "explain" && ai.explainKind === "actionable" && !root.pending
            text: qsTr("Suggest replies")
            iconName: "reply"
            onClicked: ai.suggestReplies(ai.explainedMessageId, ai.summaryName)
        }

        PillButton {
            objectName: "exportEvents"
            visible: root.kind === "events" && ai.hasEvents && !root.pending
            text: qsTr("Add to calendar")
            iconName: "calendar"
            onClicked: ai.exportEvents()
        }

        // Questions: about the chat ("ask") or about one file ("doc:<id>").
        RowLayout {
            Layout.fillWidth: true
            visible: ai.canAsk
            spacing: 6

            TextField {
                id: questionField
                objectName: "questionField"
                Layout.fillWidth: true
                placeholderText: root.kind === "doc" ? qsTr("Ask about the file")
                                                     : qsTr("Ask about this chat")
                enabled: !root.pending && ai.enabled
                color: Theme.text
                placeholderTextColor: Theme.textMuted
                font.pixelSize: Theme.fontBody
                selectByMouse: true
                background: Rectangle {
                    implicitHeight: 32
                    radius: 8
                    color: Theme.field
                    border.width: 1
                    border.color: questionField.activeFocus ? Theme.accent : Theme.fieldBorder
                }
                onAccepted: {
                    if (text.trim() !== "") {
                        ai.ask(text)
                        text = ""
                    }
                }
            }
            PillButton {
                text: qsTr("Ask")
                filled: true
                enabled: questionField.enabled && questionField.text.trim() !== ""
                onClicked: questionField.accepted()
            }
        }

        Text {
            Layout.fillWidth: true
            text: qsTr("Made by %1 via OpenRouter. Can be wrong: check the linked messages.")
                  .arg(root.kind === "explain" ? ai.cheapModel : ai.summaryModel)
            wrapMode: Text.Wrap
            color: Theme.textMuted
            font.pixelSize: 11
        }
    }
}
