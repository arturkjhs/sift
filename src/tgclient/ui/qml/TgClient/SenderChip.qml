import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts

// The "From:" filter of a search: a person button that opens a picker, or, once someone is
// picked, a chip "From: <name>" with a cross.
Item {
    id: root
    property var chatId: 0          // whose members to offer (0: anyone known)
    property string senderName: ""  // "" = no filter
    signal picked(string sender, string name)
    signal cleared()

    implicitWidth: root.senderName !== "" ? chip.implicitWidth : pickButton.implicitWidth
    implicitHeight: 30

    function openPicker() {
        senderPicker.find("", root.chatId)
        picker.open()
        pickerSearch.focusInput()
    }

    IconButton {
        id: pickButton
        objectName: "senderPickButton"
        anchors.verticalCenter: parent.verticalCenter
        visible: root.senderName === ""
        iconName: "person"
        glyphSize: 16
        Accessible.name: qsTr("Only messages from…")
        onClicked: root.openPicker()
    }

    Rectangle {
        id: chip
        objectName: "senderChip"
        anchors.verticalCenter: parent.verticalCenter
        visible: root.senderName !== ""
        implicitWidth: chipRow.implicitWidth + 16
        height: 28
        radius: 14
        color: Qt.rgba(Theme.accent.r, Theme.accent.g, Theme.accent.b, 0.15)
        border.width: 1
        border.color: Theme.accent
        Row {
            id: chipRow
            x: 10
            anchors.verticalCenter: parent.verticalCenter
            spacing: 4
            Text {
                anchors.verticalCenter: parent.verticalCenter
                text: qsTr("From: %1").arg(root.senderName)
                textFormat: Text.PlainText
                color: Theme.accent
                font.pixelSize: Theme.fontSmall
                font.weight: Font.DemiBold
                HoverHandler { cursorShape: Qt.PointingHandCursor }
                TapHandler { onTapped: root.openPicker() }
            }
            IconButton {
                objectName: "senderChipClear"
                anchors.verticalCenter: parent.verticalCenter
                width: 20
                height: 20
                iconName: "close"
                glyphSize: 9
                Accessible.name: qsTr("Any sender")
                onClicked: root.cleared()
            }
        }
    }

    Popup {
        id: picker
        objectName: "senderPicker"
        y: root.height + 6
        width: 280
        height: Math.min(360, pickerColumn.implicitHeight + 16)
        padding: 8
        focus: true
        background: Rectangle {
            radius: 10
            color: Theme.popup
            border.width: 1
            border.color: Theme.popupBorder
        }
        contentItem: ColumnLayout {
            id: pickerColumn
            spacing: 6
            SearchBox {
                id: pickerSearch
                Layout.fillWidth: true
                placeholder: qsTr("Name or @username")
                onEdited: text => senderPicker.find(text, root.chatId)
            }
            ListView {
                Layout.fillWidth: true
                Layout.preferredHeight: Math.min(contentHeight, 280)
                clip: true
                model: senderPicker.rows
                boundsBehavior: Flickable.StopAtBounds
                delegate: AbstractButton {
                    id: person
                    required property var modelData
                    width: ListView.view.width
                    height: 40
                    hoverEnabled: true
                    onClicked: {
                        root.picked(person.modelData.key, person.modelData.name)
                        picker.close()
                    }
                    background: Rectangle {
                        radius: 6
                        color: person.hovered ? Theme.hover : "transparent"
                    }
                    contentItem: Item {
                        Avatar {
                            id: personAvatar
                            x: 4
                            anchors.verticalCenter: parent.verticalCenter
                            size: 28
                            avatarSource: person.modelData.avatar
                            initials: person.modelData.initials
                            colorIndex: person.modelData.colorIndex
                        }
                        Column {
                            anchors.left: personAvatar.right
                            anchors.leftMargin: 10
                            anchors.right: parent.right
                            anchors.verticalCenter: parent.verticalCenter
                            Text {
                                width: parent.width
                                text: person.modelData.name
                                textFormat: Text.PlainText
                                elide: Text.ElideRight
                                color: Theme.text
                                font.pixelSize: Theme.fontBody
                            }
                            Text {
                                visible: text !== ""
                                text: person.modelData.username ? "@" + person.modelData.username
                                                                : ""
                                color: Theme.textMuted
                                font.pixelSize: Theme.fontSmall
                            }
                        }
                    }
                }
            }
        }
    }
}
