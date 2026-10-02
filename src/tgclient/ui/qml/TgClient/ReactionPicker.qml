import QtQuick
import QtQuick.Controls.Basic

// All reactions the chat allows, in a scrollable grid (the menu's row shows only the first few).
Popup {
    id: root
    objectName: "reactionPicker"

    property var messageId: 0
    property var reactions: []   // [{key, label}]
    property var chosen: []      // keys already set by me

    signal picked(var messageId, string key)

    function openAt(messageId, reactions, chosen) {
        root.messageId = messageId
        root.reactions = reactions
        root.chosen = chosen
        open()
    }

    readonly property int columns: 8
    readonly property int cell: 38

    width: columns * cell + 2 * padding + 8   // room for the scroll bar
    height: Math.min(grid.contentHeight, 6 * cell) + 2 * padding
    padding: 6
    modal: false
    focus: true
    closePolicy: Popup.CloseOnEscape | Popup.CloseOnPressOutside

    enter: Transition {
        NumberAnimation { property: "opacity"; from: 0; to: 1; duration: 110 }
        NumberAnimation { property: "scale"; from: 0.96; to: 1; duration: 140; easing.type: Easing.OutCubic }
    }
    exit: Transition {
        NumberAnimation { property: "opacity"; to: 0; duration: 80 }
    }

    background: Item {
        Repeater {  // soft shadow, like AppMenu
            model: 3
            Rectangle {
                required property int index
                anchors.fill: parent
                anchors.margins: -index - 1
                anchors.topMargin: -index
                anchors.bottomMargin: -index * 2 - 3
                radius: 13 + index
                color: Theme.shadow
                opacity: 0.35 - index * 0.1
            }
        }
        Rectangle {
            anchors.fill: parent
            radius: 12
            color: Theme.popup
            border.width: 1
            border.color: Theme.popupBorder
        }
    }

    contentItem: GridView {
        id: grid
        clip: true
        cellWidth: root.cell
        cellHeight: root.cell
        boundsBehavior: Flickable.StopAtBounds
        model: root.reactions
        ScrollBar.vertical: ScrollBar { policy: ScrollBar.AsNeeded }

        delegate: AbstractButton {
            id: button
            required property var modelData
            readonly property bool chosen: root.chosen.indexOf(modelData.key) >= 0
            width: root.cell
            height: root.cell
            hoverEnabled: true
            Accessible.name: modelData.label
            onClicked: {
                root.picked(root.messageId, modelData.key)
                root.close()
            }
            background: Rectangle {
                anchors.fill: parent
                anchors.margins: 2
                radius: width / 2
                color: button.chosen ? Theme.selection
                     : button.hovered ? Theme.hover : "transparent"
            }
            contentItem: Text {
                text: button.modelData.label
                horizontalAlignment: Text.AlignHCenter
                verticalAlignment: Text.AlignVCenter
                font.pixelSize: button.hovered ? 24 : 21
                Behavior on font.pixelSize { NumberAnimation { duration: 80 } }
            }
        }
    }
}
