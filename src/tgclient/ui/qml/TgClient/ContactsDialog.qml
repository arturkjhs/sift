import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts

// Contacts: search, open a chat, add one by phone number.
Popup {
    id: root
    objectName: "contactsDialog"
    property bool adding: false
    property string error: ""

    function show() {
        root.adding = false
        root.error = ""
        searchBox.clear()
        contacts.search("")
        open()
        searchBox.focusInput()
    }

    Connections {
        target: contacts
        function onChatReady(chatId) { root.close() }
        function onFailed(message) { root.error = message }
    }

    parent: Overlay.overlay
    anchors.centerIn: parent
    width: Math.min(400, parent ? parent.width - 48 : 400)
    height: Math.min(560, parent ? parent.height - 48 : 560)
    modal: true
    padding: 0
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
        spacing: 0
        RowLayout {
            Layout.fillWidth: true
            Layout.margins: 16
            Layout.bottomMargin: 8
            Text {
                Layout.fillWidth: true
                text: root.adding ? qsTr("New contact") : qsTr("Contacts")
                color: Theme.text
                font.pixelSize: 16
                font.weight: Font.DemiBold
            }
            PillButton {
                visible: !root.adding
                objectName: "addContact"
                text: qsTr("Add")
                iconName: "person"
                onClicked: root.adding = true
            }
            IconButton {
                iconName: "close"
                glyphSize: 12
                Accessible.name: qsTr("Close")
                onClicked: root.close()
            }
        }

        ColumnLayout {  // adding by phone number
            Layout.fillWidth: true
            Layout.leftMargin: 16
            Layout.rightMargin: 16
            visible: root.adding
            spacing: 8
            Field { id: phone; objectName: "contactPhone"; placeholderText: qsTr("Phone number") }
            Field { id: firstName; objectName: "contactFirst"; placeholderText: qsTr("First name") }
            Field { id: lastName; placeholderText: qsTr("Last name (optional)") }
            RowLayout {
                Layout.fillWidth: true
                Item { Layout.fillWidth: true }
                PillButton {
                    text: qsTr("Cancel")
                    onClicked: root.adding = false
                }
                PillButton {
                    objectName: "saveContact"
                    text: qsTr("Add")
                    filled: true
                    enabled: phone.text.trim() !== "" && firstName.text.trim() !== ""
                    onClicked: {
                        root.error = ""
                        contacts.add(phone.text, firstName.text, lastName.text)
                    }
                }
            }
        }

        Text {
            Layout.fillWidth: true
            Layout.leftMargin: 16
            Layout.rightMargin: 16
            visible: root.error !== ""
            text: root.error
            wrapMode: Text.Wrap
            color: Theme.danger
            font.pixelSize: Theme.fontSmall
        }

        SearchBox {
            id: searchBox
            Layout.fillWidth: true
            Layout.leftMargin: 16
            Layout.rightMargin: 16
            Layout.bottomMargin: 8
            visible: !root.adding
            placeholder: qsTr("Search contacts")
            onEdited: text => contacts.search(text)
        }

        ListView {
            id: list
            Layout.fillWidth: true
            Layout.fillHeight: true
            visible: !root.adding
            clip: true
            model: contacts.rows
            boundsBehavior: Flickable.StopAtBounds
            ScrollBar.vertical: ScrollBar {}
            delegate: AbstractButton {
                id: row
                required property var modelData
                width: ListView.view.width
                height: 52
                hoverEnabled: true
                onClicked: contacts.openChat(row.modelData.userId)
                background: Rectangle { color: row.hovered ? Theme.hover : "transparent" }
                contentItem: Item {
                    Avatar {
                        id: avatar
                        x: 16
                        anchors.verticalCenter: parent.verticalCenter
                        size: 36
                        avatarSource: row.modelData.avatar
                        initials: row.modelData.initials
                        colorIndex: row.modelData.colorIndex
                    }
                    Column {
                        anchors.left: avatar.right
                        anchors.leftMargin: 12
                        anchors.right: parent.right
                        anchors.rightMargin: 16
                        anchors.verticalCenter: parent.verticalCenter
                        Text {
                            width: parent.width
                            text: row.modelData.name
                            textFormat: Text.PlainText
                            elide: Text.ElideRight
                            color: Theme.text
                            font.pixelSize: Theme.fontTitle
                        }
                        Text {
                            text: row.modelData.status
                            color: row.modelData.online ? Theme.accent : Theme.textMuted
                            font.pixelSize: Theme.fontSmall
                        }
                    }
                }
            }
            Text {
                anchors.centerIn: parent
                visible: list.count === 0
                text: contacts.busy ? qsTr("Loading…") : qsTr("No contacts found")
                color: Theme.textMuted
                font.pixelSize: Theme.fontBody
            }
        }
    }
}
