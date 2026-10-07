import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts

// A new group (name + people from the contacts) or channel (name + description); or, with
// mode "add", people to add to an existing group.
Popup {
    id: root
    objectName: "newChatDialog"
    property string mode: "group"   // group | channel | add
    property var chatId: 0           // "add": the group
    property var chosen: []
    property string error: ""

    function start(mode, chatId) {
        root.mode = mode
        root.chatId = chatId || 0
        root.chosen = []
        root.error = ""
        title.text = ""
        description.text = ""
        contacts.search("")
        open()
        if (mode !== "add")
            title.forceActiveFocus()
    }

    function toggle(userId) {
        const at = chosen.indexOf(userId)
        chosen = at >= 0 ? chosen.filter(id => id !== userId) : chosen.concat([userId])
    }

    Connections {
        target: groupAdmin
        function onCreated(chatId) { root.close() }
        function onFailed(message) { if (root.opened) root.error = message }
    }

    parent: Overlay.overlay
    anchors.centerIn: parent
    width: Math.min(400, parent ? parent.width - 48 : 400)
    height: Math.min(root.mode === "channel" ? 300 : 560, parent ? parent.height - 48 : 560)
    modal: true
    padding: 18
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

    contentItem: ColumnLayout {
        spacing: 8
        Text {
            text: root.mode === "channel" ? qsTr("New channel")
                  : root.mode === "add" ? qsTr("Add members") : qsTr("New group")
            color: Theme.text
            font.pixelSize: 16
            font.weight: Font.DemiBold
        }
        Field {
            id: title
            objectName: "newChatTitle"
            visible: root.mode !== "add"
            placeholderText: root.mode === "channel" ? qsTr("Channel name") : qsTr("Group name")
            maximumLength: 128
        }
        Field {
            id: description
            visible: root.mode === "channel"
            placeholderText: qsTr("Description (optional)")
            maximumLength: 255
        }
        Text {
            visible: root.mode !== "channel"
            text: qsTr("People (%1)").arg(root.chosen.length)
            color: Theme.textMuted
            font.pixelSize: Theme.fontSmall
            font.weight: Font.DemiBold
        }
        SearchBox {
            Layout.fillWidth: true
            visible: root.mode !== "channel"
            placeholder: qsTr("Search contacts")
            onEdited: text => contacts.search(text)
        }
        ListView {
            Layout.fillWidth: true
            Layout.fillHeight: true
            visible: root.mode !== "channel"
            clip: true
            model: root.mode !== "channel" ? contacts.rows : []
            boundsBehavior: Flickable.StopAtBounds
            ScrollBar.vertical: ScrollBar {}
            delegate: AbstractButton {
                id: person
                required property var modelData
                readonly property bool picked: root.chosen.indexOf(modelData.userId) >= 0
                width: ListView.view.width
                height: 42
                hoverEnabled: true
                onClicked: root.toggle(person.modelData.userId)
                background: Rectangle { color: person.hovered ? Theme.hover : "transparent" }
                contentItem: Item {
                    Avatar {
                        id: personAvatar
                        x: 4
                        anchors.verticalCenter: parent.verticalCenter
                        size: 30
                        avatarSource: person.modelData.avatar
                        initials: person.modelData.initials
                        colorIndex: person.modelData.colorIndex
                    }
                    Text {
                        anchors.left: personAvatar.right
                        anchors.leftMargin: 10
                        anchors.right: box.left
                        anchors.verticalCenter: parent.verticalCenter
                        text: person.modelData.name
                        textFormat: Text.PlainText
                        elide: Text.ElideRight
                        color: Theme.text
                        font.pixelSize: Theme.fontBody
                    }
                    Rectangle {
                        id: box
                        anchors.right: parent.right
                        anchors.rightMargin: 6
                        anchors.verticalCenter: parent.verticalCenter
                        width: 18
                        height: 18
                        radius: 5
                        color: person.picked ? Theme.accent : "transparent"
                        border.width: 1.5
                        border.color: Theme.accent
                        Icon {
                            anchors.centerIn: parent
                            visible: person.picked
                            name: "check"
                            color: Theme.textOnAccent
                            size: 14
                        }
                    }
                }
            }
        }
        Item { Layout.fillHeight: true; visible: root.mode === "channel" }
        Text {
            Layout.fillWidth: true
            visible: root.error !== ""
            text: root.error
            wrapMode: Text.Wrap
            color: Theme.danger
            font.pixelSize: Theme.fontSmall
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
                objectName: "createChat"
                filled: true
                text: root.mode === "add" ? qsTr("Add") : qsTr("Create")
                enabled: root.mode === "add" ? root.chosen.length > 0 : title.text.trim() !== ""
                onClicked: {
                    root.error = ""
                    if (root.mode === "add") {
                        groupAdmin.addMembers(root.chatId, root.chosen)
                        root.close()
                    } else {
                        groupAdmin.create(root.mode, title.text, description.text, root.chosen)
                    }
                }
            }
        }
    }
}
