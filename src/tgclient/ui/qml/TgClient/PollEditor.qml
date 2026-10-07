import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts

// New poll or quiz: a question, 2–10 options, anonymous / several answers / quiz with the
// right answer and an explanation.
Popup {
    id: root
    objectName: "pollEditor"
    property var options: ["", ""]
    property int correct: 0

    function start() {
        question.text = ""
        root.options = ["", ""]
        root.correct = 0
        anonymous.checked = true
        multiple.checked = false
        quiz.checked = false
        explanation.text = ""
        open()
        question.forceActiveFocus()
    }

    function setOption(index, text) {
        const copy = root.options.slice()
        copy[index] = text
        if (index === copy.length - 1 && text !== "" && copy.length < 10)
            copy.push("")  // a new empty field appears as the last one gets text
        root.options = copy
    }

    readonly property var filled: options.filter(o => o.trim() !== "")
    readonly property bool valid: question.text.trim() !== "" && filled.length >= 2

    parent: Overlay.overlay
    anchors.centerIn: parent
    width: Math.min(420, parent ? parent.width - 48 : 420)
    height: Math.min(column.implicitHeight + 40, parent ? parent.height - 48 : 600)
    modal: true
    padding: 20
    background: Rectangle {
        radius: 12
        color: Theme.sidebar
        border.width: 1
        border.color: Theme.separator
    }

    component Field: TextField {
        Layout.fillWidth: true
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

    contentItem: Flickable {
        clip: true
        contentHeight: column.implicitHeight
        boundsBehavior: Flickable.StopAtBounds
        ColumnLayout {
            id: column
            width: parent.width
            spacing: 8
            Text {
                text: quiz.checked ? qsTr("New quiz") : qsTr("New poll")
                color: Theme.text
                font.pixelSize: 16
                font.weight: Font.DemiBold
            }
            Field {
                id: question
                objectName: "pollQuestion"
                placeholderText: qsTr("Question")
                maximumLength: 300
            }
            Text {
                text: quiz.checked ? qsTr("Options (tick the right one)") : qsTr("Options")
                color: Theme.textMuted
                font.pixelSize: Theme.fontSmall
                font.weight: Font.DemiBold
            }
            Repeater {
                model: root.options.length
                RowLayout {
                    id: optionRow
                    required property int index
                    Layout.fillWidth: true
                    spacing: 8
                    Rectangle {
                        visible: quiz.checked
                        width: 18
                        height: 18
                        radius: 9
                        color: root.correct === optionRow.index ? Theme.accent : "transparent"
                        border.width: 1.5
                        border.color: Theme.accent
                        TapHandler { onTapped: root.correct = optionRow.index }
                    }
                    Field {
                        objectName: "pollOption"
                        placeholderText: qsTr("Option %1").arg(optionRow.index + 1)
                        maximumLength: 100
                        text: root.options[optionRow.index]
                        onTextEdited: root.setOption(optionRow.index, text)
                    }
                }
            }
            RowLayout {
                spacing: 10
                ToggleSwitch { id: anonymous; checked: true }
                Text { text: qsTr("Anonymous voting"); color: Theme.text; font.pixelSize: Theme.fontBody }
            }
            RowLayout {
                spacing: 10
                visible: !quiz.checked
                ToggleSwitch { id: multiple }
                Text { text: qsTr("Several answers"); color: Theme.text; font.pixelSize: Theme.fontBody }
            }
            RowLayout {
                spacing: 10
                ToggleSwitch { id: quiz }
                Text { text: qsTr("Quiz mode"); color: Theme.text; font.pixelSize: Theme.fontBody }
            }
            Field {
                id: explanation
                visible: quiz.checked
                placeholderText: qsTr("Explanation (optional)")
                maximumLength: 200
            }
            RowLayout {
                Layout.fillWidth: true
                spacing: 8
                Item { Layout.fillWidth: true }
                PillButton {
                    text: qsTr("Cancel")
                    onClicked: root.close()
                }
                PillButton {
                    objectName: "sendPoll"
                    text: qsTr("Send")
                    filled: true
                    enabled: root.valid
                    onClicked: {
                        const filled = []
                        let correct = 0
                        root.options.forEach((o, i) => {
                            if (o.trim() !== "") {
                                if (i === root.correct)
                                    correct = filled.length
                                filled.push(o)
                            }
                        })
                        messages.sendPoll({question: question.text, options: filled,
                                           anonymous: anonymous.checked,
                                           multiple: multiple.checked, quiz: quiz.checked,
                                           correct: correct, explanation: explanation.text})
                        root.close()
                    }
                }
            }
        }
    }
}
