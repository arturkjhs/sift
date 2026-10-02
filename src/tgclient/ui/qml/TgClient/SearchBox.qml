import QtQuick
import QtQuick.Controls.Basic

// Rounded search input with an icon and a clear button. Esc clears, then gives focus back.
FocusScope {
    id: root
    property alias text: input.text
    property string placeholder: qsTr("Search")
    signal edited(string text)
    signal cleared()

    implicitHeight: 34

    function focusInput() {
        input.forceActiveFocus()
        input.selectAll()
    }

    function clear() {
        input.text = ""
        root.edited("")
        root.cleared()
    }

    Rectangle {
        anchors.fill: parent
        radius: 8
        color: input.activeFocus ? Theme.field : Theme.hover
        border.width: 1
        border.color: input.activeFocus ? Theme.accent : "transparent"

        Icon {
            id: icon
            anchors.left: parent.left
            anchors.leftMargin: 9
            anchors.verticalCenter: parent.verticalCenter
            name: "search"
            size: 16
        }

        TextInput {
            id: input
            objectName: "searchInput"
            anchors.left: icon.right
            anchors.leftMargin: 7
            anchors.right: clearButton.left
            anchors.verticalCenter: parent.verticalCenter
            clip: true
            focus: true
            color: Theme.text
            selectionColor: Theme.accent
            selectedTextColor: Theme.textOnAccent
            font.pixelSize: Theme.fontBody
            selectByMouse: true
            onTextEdited: root.edited(text)
            Keys.onEscapePressed: root.clear()

            Text {
                anchors.verticalCenter: parent.verticalCenter
                visible: input.text === ""
                text: root.placeholder
                color: Theme.textMuted
                font.pixelSize: Theme.fontBody
            }
        }

        IconButton {
            id: clearButton
            anchors.right: parent.right
            anchors.rightMargin: 3
            anchors.verticalCenter: parent.verticalCenter
            width: visible ? 26 : 0
            height: 26
            visible: input.text !== ""
            iconName: "close"
            glyphSize: 11
            Accessible.name: qsTr("Clear search")
            onClicked: root.clear()
        }
    }
}
