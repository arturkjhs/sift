import QtQuick
import QtQuick.Controls.Basic

// A poll, quiz or checklist inside a bubble. Single-answer polls vote on click; multiple-answer
// ones collect ticks and vote with the button. Results (bars, percents) show after voting.
Column {
    id: root
    property var poll: ({})
    property var messageId: 0
    readonly property bool checklist: poll.kind === "checklist"
    property var picked: []  // multiple answers being chosen

    width: parent ? parent.width : 300
    spacing: 6
    visible: (poll.kind || "") !== ""

    function toggle(index) {
        const at = picked.indexOf(index)
        picked = at >= 0 ? picked.filter(i => i !== index) : picked.concat([index])
    }

    Text {
        width: parent.width
        text: root.poll.question || ""
        textFormat: Text.PlainText
        wrapMode: Text.Wrap
        color: Theme.text
        font.pixelSize: Theme.fontTitle
        font.weight: Font.DemiBold
    }
    Text {
        text: root.checklist ? qsTr("Checklist · %1 of %2 done").arg(root.poll.doneCount)
                                                                 .arg(root.poll.tasks.length)
              : (root.poll.kind === "quiz" ? qsTr("Quiz") : root.poll.anonymous
                 ? qsTr("Anonymous poll") : qsTr("Poll"))
                + (root.poll.closed ? " · " + qsTr("closed") : "")
        color: Theme.textMuted
        font.pixelSize: Theme.fontSmall
    }

    // --- poll options -----------------------------------------------------------------------
    Repeater {
        model: root.checklist ? [] : (root.poll.options || [])
        Item {
            id: option
            required property var modelData
            readonly property bool results: root.poll.showResults
            width: root.width
            height: Math.max(30, optionText.implicitHeight + 10)

            Rectangle {  // the result bar
                visible: option.results
                anchors.left: optionText.left
                anchors.bottom: parent.bottom
                width: Math.max(3, (root.width - optionText.x) * option.modelData.percent / 100)
                height: 4
                radius: 2
                color: option.modelData.correct ? Theme.accent
                     : root.poll.kind === "quiz" && option.modelData.chosen ? Theme.danger
                     : Theme.accent
                opacity: option.modelData.chosen || option.modelData.correct ? 1 : 0.45
            }
            Text {  // percent, or the circle / box to choose
                id: marker
                width: 34
                anchors.verticalCenter: optionText.verticalCenter
                horizontalAlignment: Text.AlignRight
                visible: option.results
                text: option.modelData.percent + "%"
                color: Theme.text
                font.pixelSize: Theme.fontSmall
                font.weight: Font.DemiBold
            }
            Rectangle {
                visible: !option.results
                x: 12
                anchors.verticalCenter: optionText.verticalCenter
                width: 18
                height: 18
                radius: root.poll.multiple ? 4 : 9
                color: root.picked.indexOf(option.modelData.index) >= 0 ? Theme.accent
                                                                        : "transparent"
                border.width: 1.5
                border.color: Theme.accent
            }
            Text {
                id: optionText
                x: 44
                width: root.width - x - (checkIcon.visible ? 22 : 0)
                y: 3
                text: option.modelData.text
                textFormat: Text.PlainText
                wrapMode: Text.Wrap
                color: Theme.text
                font.pixelSize: Theme.fontBody
            }
            Icon {
                id: checkIcon
                anchors.right: parent.right
                anchors.verticalCenter: optionText.verticalCenter
                visible: option.results && (option.modelData.chosen || option.modelData.correct)
                name: "check"
                color: root.poll.kind === "quiz" && option.modelData.chosen
                       && !option.modelData.correct ? Theme.danger : Theme.accent
                size: 16
            }
            HoverHandler {
                enabled: root.poll.canVote && !option.results
                cursorShape: Qt.PointingHandCursor
            }
            TapHandler {
                enabled: root.poll.canVote && !option.results
                onTapped: root.poll.multiple ? root.toggle(option.modelData.index)
                                             : messages.vote(root.messageId,
                                                             [option.modelData.index])
            }
        }
    }

    // --- checklist tasks --------------------------------------------------------------------
    Repeater {
        model: root.checklist ? root.poll.tasks : []
        Item {
            id: task
            required property var modelData
            width: root.width
            height: Math.max(28, taskColumn.implicitHeight + 6)
            Rectangle {
                x: 2
                y: 5
                width: 18
                height: 18
                radius: 5
                color: task.modelData.done ? Theme.accent : "transparent"
                border.width: 1.5
                border.color: Theme.accent
                Icon {
                    anchors.centerIn: parent
                    visible: task.modelData.done
                    name: "check"
                    color: Theme.textOnAccent
                    size: 14
                }
            }
            Column {
                id: taskColumn
                x: 30
                y: 3
                width: root.width - x
                Text {
                    width: parent.width
                    text: task.modelData.text
                    textFormat: Text.PlainText
                    wrapMode: Text.Wrap
                    color: task.modelData.done ? Theme.textMuted : Theme.text
                    font.strikeout: task.modelData.done
                    font.pixelSize: Theme.fontBody
                }
                Text {
                    visible: task.modelData.done && task.modelData.doneBy !== ""
                    text: qsTr("done by %1").arg(task.modelData.doneBy)
                    color: Theme.textMuted
                    font.pixelSize: Theme.fontSmall
                }
            }
            HoverHandler {
                enabled: root.poll.canMark === true
                cursorShape: Qt.PointingHandCursor
            }
            TapHandler {
                enabled: root.poll.canMark === true
                onTapped: messages.markTask(root.messageId, task.modelData.id,
                                            !task.modelData.done)
            }
        }
    }

    Row {
        spacing: 10
        Text {
            anchors.verticalCenter: parent.verticalCenter
            visible: !root.checklist
            text: root.poll.total === 1 ? qsTr("1 vote") : qsTr("%1 votes").arg(root.poll.total || 0)
            color: Theme.textMuted
            font.pixelSize: Theme.fontSmall
        }
        PillButton {
            objectName: "voteButton"
            visible: root.poll.multiple === true && !root.poll.showResults && root.poll.canVote
            enabled: root.picked.length > 0
            filled: true
            text: qsTr("Vote")
            onClicked: {
                messages.vote(root.messageId, root.picked)
                root.picked = []
            }
        }
        Text {
            anchors.verticalCenter: parent.verticalCenter
            visible: root.poll.voted === true && root.poll.canVote === true
            text: qsTr("Retract vote")
            color: Theme.accent
            font.pixelSize: Theme.fontSmall
            HoverHandler { cursorShape: Qt.PointingHandCursor }
            TapHandler { onTapped: messages.vote(root.messageId, []) }
        }
    }
    Text {
        width: parent.width
        visible: text !== ""
        text: root.poll.explanation || ""
        textFormat: Text.PlainText
        wrapMode: Text.Wrap
        color: Theme.textMuted
        font.pixelSize: Theme.fontSmall
        font.italic: true
    }
}
