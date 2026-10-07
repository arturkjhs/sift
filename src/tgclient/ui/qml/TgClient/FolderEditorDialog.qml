import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts

// Create or edit a chat folder: its name, which kinds of chats it takes, which chats exactly.
Popup {
    id: root
    objectName: "folderEditorDialog"
    property var folder: ({})
    property var chosen: []   // chat ids included by hand
    property string error: ""

    function editFolder(key) {
        root.error = ""
        folderEditor.edit(key)
    }

    function toggleChat(chatId) {
        const at = chosen.indexOf(chatId)
        chosen = at >= 0 ? chosen.filter(id => id !== chatId) : chosen.concat([chatId])
    }

    function setFlag(name, value) {
        const copy = Object.assign({}, folder)
        copy[name] = value
        folder = copy
    }

    Connections {
        target: folderEditor
        function onLoaded(folder) {
            root.folder = folder
            root.chosen = folder.chats || []
            nameField.text = folder.name || ""
            chatPicker.filter = ""
            pickerSearch.clear()
            chatPicker.refresh()
            root.open()
        }
        function onFailed(message) { root.error = message }
    }

    parent: Overlay.overlay
    anchors.centerIn: parent
    width: Math.min(440, parent ? parent.width - 48 : 440)
    height: Math.min(620, parent ? parent.height - 48 : 620)
    modal: true
    padding: 18
    background: Rectangle {
        radius: 12
        color: Theme.sidebar
        border.width: 1
        border.color: Theme.separator
    }

    component Flag: RowLayout {
        property string flag: ""
        property alias label: flagText.text
        Layout.fillWidth: true
        spacing: 10
        Text {
            id: flagText
            Layout.fillWidth: true
            color: Theme.text
            font.pixelSize: Theme.fontBody
        }
        ToggleSwitch {
            checked: root.folder[parent.flag] === true
            onToggled: root.setFlag(parent.flag, checked)
        }
    }

    contentItem: ColumnLayout {
        spacing: 8
        Text {
            text: root.folder.id ? qsTr("Edit folder") : qsTr("New folder")
            color: Theme.text
            font.pixelSize: 16
            font.weight: Font.DemiBold
        }
        TextField {
            id: nameField
            objectName: "folderName"
            Layout.fillWidth: true
            placeholderText: qsTr("Folder name")
            maximumLength: 12
            color: Theme.text
            placeholderTextColor: Theme.textMuted
            font.pixelSize: Theme.fontBody
            padding: 8
            background: Rectangle {
                radius: 8
                color: Theme.field
                border.width: 1
                border.color: nameField.activeFocus ? Theme.accent : Theme.fieldBorder
            }
        }
        Text {
            text: qsTr("Chat types")
            color: Theme.textMuted
            font.pixelSize: Theme.fontSmall
            font.weight: Font.DemiBold
        }
        Flag { flag: "includeContacts"; label: qsTr("Contacts") }
        Flag { flag: "includeNonContacts"; label: qsTr("Non-contacts") }
        Flag { flag: "includeGroups"; label: qsTr("Groups") }
        Flag { flag: "includeChannels"; label: qsTr("Channels") }
        Flag { flag: "includeBots"; label: qsTr("Bots") }
        Flag { flag: "excludeMuted"; label: qsTr("Leave out muted") }
        Flag { flag: "excludeRead"; label: qsTr("Leave out read") }
        Text {
            Layout.topMargin: 4
            text: qsTr("Chats (%1)").arg(root.chosen.length)
            color: Theme.textMuted
            font.pixelSize: Theme.fontSmall
            font.weight: Font.DemiBold
        }
        SearchBox {
            id: pickerSearch
            Layout.fillWidth: true
            placeholder: qsTr("Search chats")
            onEdited: text => chatPicker.filter = text
        }
        ListView {
            Layout.fillWidth: true
            Layout.fillHeight: true
            Layout.minimumHeight: 120
            clip: true
            model: chatPicker
            boundsBehavior: Flickable.StopAtBounds
            ScrollBar.vertical: ScrollBar {}
            delegate: AbstractButton {
                id: chatRow
                required property var chatId
                required property string title
                required property string avatarSource
                required property string initials
                required property int colorIndex
                readonly property bool picked: root.chosen.indexOf(chatId) >= 0
                width: ListView.view.width
                height: 40
                hoverEnabled: true
                onClicked: root.toggleChat(chatRow.chatId)
                background: Rectangle { color: chatRow.hovered ? Theme.hover : "transparent" }
                contentItem: Item {
                    Avatar {
                        id: chatAvatar
                        x: 4
                        anchors.verticalCenter: parent.verticalCenter
                        size: 28
                        avatarSource: chatRow.avatarSource
                        initials: chatRow.initials
                        colorIndex: chatRow.colorIndex
                    }
                    Text {
                        anchors.left: chatAvatar.right
                        anchors.leftMargin: 10
                        anchors.right: check.left
                        anchors.verticalCenter: parent.verticalCenter
                        text: chatRow.title
                        textFormat: Text.PlainText
                        elide: Text.ElideRight
                        color: Theme.text
                        font.pixelSize: Theme.fontBody
                    }
                    Rectangle {
                        id: check
                        anchors.right: parent.right
                        anchors.rightMargin: 6
                        anchors.verticalCenter: parent.verticalCenter
                        width: 18
                        height: 18
                        radius: 5
                        color: chatRow.picked ? Theme.accent : "transparent"
                        border.width: 1.5
                        border.color: Theme.accent
                        Icon {
                            anchors.centerIn: parent
                            visible: chatRow.picked
                            name: "check"
                            color: Theme.textOnAccent
                            size: 14
                        }
                    }
                }
            }
        }
        Text {
            visible: root.error !== ""
            text: root.error
            color: Theme.danger
            font.pixelSize: Theme.fontSmall
        }
        RowLayout {
            Layout.fillWidth: true
            spacing: 8
            PillButton {
                visible: (root.folder.id || 0) !== 0
                text: qsTr("Delete")
                danger: true
                onClicked: {
                    folderEditor.remove("folder:" + root.folder.id)
                    root.close()
                }
            }
            Item { Layout.fillWidth: true }
            PillButton {
                text: qsTr("Cancel")
                onClicked: root.close()
            }
            PillButton {
                objectName: "saveFolder"
                text: qsTr("Save")
                filled: true
                enabled: nameField.text.trim() !== ""
                onClicked: {
                    const folder = Object.assign({}, root.folder)
                    folder.name = nameField.text
                    folder.chats = root.chosen
                    root.error = ""
                    folderEditor.save(folder)
                    root.close()
                }
            }
        }
    }
}
