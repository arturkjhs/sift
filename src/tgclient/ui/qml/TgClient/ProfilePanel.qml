import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts

// A chat's or a person's profile on the right of the feed: who, what about, members, groups
// in common, and what was shared.
Rectangle {
    id: root
    objectName: "profilePanel"
    color: Theme.sidebar

    signal closeRequested()
    signal messageRequested(var chatId, var messageId)
    signal chatRequested(var chatId)
    signal personRequested(var userId)
    signal searchRequested()
    signal addMembersRequested(var chatId)
    signal personMessagesRequested(var chatId, string senderKey, string name)

    // My role in the group or channel (owner / admin / member / ""), and its invite link.
    readonly property string myRole: (groupAdmin.revision, profile.isUser ? ""
                                       : groupAdmin.role(profile.chatId))
    readonly property bool isAdmin: myRole === "owner" || myRole === "admin"
    readonly property string inviteLink: (groupAdmin.revision, isAdmin
                                          ? groupAdmin.inviteLink(profile.chatId) : "")
    property bool editing: false
    property var memberMenuUser: 0

    readonly property var shownChat: profile.chatId
    onShownChatChanged: root.editing = false

    component Field: ColumnLayout {
        property alias label: fieldLabel.text
        property alias value: fieldValue.text
        Layout.fillWidth: true
        visible: fieldValue.text !== ""
        spacing: 1
        TextEdit {
            id: fieldValue
            Layout.fillWidth: true
            readOnly: true
            selectByMouse: true
            wrapMode: TextEdit.Wrap
            textFormat: TextEdit.PlainText
            color: Theme.text
            selectionColor: Theme.accent
            selectedTextColor: Theme.textOnAccent
            font.pixelSize: Theme.fontBody
        }
        Text {
            id: fieldLabel
            color: Theme.textMuted
            font.pixelSize: Theme.fontSmall
        }
    }

    component PersonRow: Rectangle {
        id: personRow
        property string title: ""
        property string detail: ""
        property string badge: ""
        property string avatar: ""
        property string initials: ""
        property int colorIndex: 0
        signal clicked()
        Layout.fillWidth: true
        implicitHeight: 46
        radius: 8
        color: rowHover.hovered ? Theme.hover : "transparent"
        HoverHandler { id: rowHover; cursorShape: Qt.PointingHandCursor }
        TapHandler { onTapped: personRow.clicked() }
        Avatar {
            x: 4
            anchors.verticalCenter: parent.verticalCenter
            size: 34
            avatarSource: personRow.avatar
            initials: personRow.initials
            colorIndex: personRow.colorIndex
        }
        Column {
            x: 48
            width: parent.width - 52 - (badgeText.visible ? badgeText.implicitWidth + 8 : 0)
            anchors.verticalCenter: parent.verticalCenter
            Text {
                width: parent.width
                text: personRow.title
                textFormat: Text.PlainText
                elide: Text.ElideRight
                color: Theme.text
                font.pixelSize: Theme.fontBody
                font.weight: Font.DemiBold
            }
            Text {
                width: parent.width
                visible: text !== ""
                text: personRow.detail
                elide: Text.ElideRight
                color: Theme.textMuted
                font.pixelSize: Theme.fontSmall
            }
        }
        Text {
            id: badgeText
            anchors.right: parent.right
            anchors.rightMargin: 6
            anchors.verticalCenter: parent.verticalCenter
            visible: text !== ""
            text: personRow.badge
            color: Theme.accent
            font.pixelSize: Theme.fontSmall
        }
    }

    AppMenu {
        id: memberMenu
        objectName: "memberMenu"
        AppMenuItem {
            visible: root.myRole === "owner" && (root.memberMenuUser.role || "") !== "admin"
            text: qsTr("Make admin")
            iconName: "person"
            onTriggered: groupAdmin.setAdmin(profile.chatId, root.memberMenuUser.userId, true)
        }
        AppMenuItem {
            visible: root.myRole === "owner" && (root.memberMenuUser.role || "") === "admin"
            text: qsTr("Remove admin rights")
            iconName: "person"
            onTriggered: groupAdmin.setAdmin(profile.chatId, root.memberMenuUser.userId, false)
        }
        AppMenuItem {
            text: qsTr("Remove from the group")
            iconName: "close"
            danger: true
            onTriggered: adminConfirm.ask(
                qsTr("Remove %1 from the group?").arg(root.memberMenuUser.name || ""), "",
                qsTr("Remove"), "", {remove: root.memberMenuUser.userId})
        }
    }

    ConfirmDialog {
        id: adminConfirm
        onAccepted: (checked, payload) => {
            if (payload !== null && typeof payload === "object" && payload.remove)
                groupAdmin.removeMember(profile.chatId, payload.remove)
            else
                groupAdmin.deleteChat(payload)
        }
    }

    ColumnLayout {
        anchors.fill: parent
        anchors.margins: 14
        spacing: 6

        RowLayout {
            Layout.fillWidth: true
            Text {
                Layout.fillWidth: true
                text: profile.isUser ? qsTr("User info") : qsTr("Chat info")
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

        Flickable {
            id: flick
            Layout.fillWidth: true
            Layout.fillHeight: true
            clip: true
            contentWidth: width
            contentHeight: column.implicitHeight
            boundsBehavior: Flickable.StopAtBounds
            ScrollBar.vertical: ScrollBar {}
            onAtYEndChanged: if (atYEnd) profile.loadMoreShared()

            ColumnLayout {
                id: column
                width: flick.width
                spacing: 10

                Avatar {
                    Layout.alignment: Qt.AlignHCenter
                    Layout.topMargin: 6
                    size: 84
                    avatarSource: profile.avatar
                    initials: profile.initials
                    colorIndex: profile.colorIndex
                }
                Text {
                    Layout.fillWidth: true
                    horizontalAlignment: Text.AlignHCenter
                    wrapMode: Text.Wrap
                    text: profile.title
                    textFormat: Text.PlainText
                    color: Theme.text
                    font.pixelSize: 17
                    font.weight: Font.DemiBold
                }
                Text {
                    Layout.fillWidth: true
                    horizontalAlignment: Text.AlignHCenter
                    visible: text !== ""
                    text: profile.subtitle
                    color: Theme.textMuted
                    font.pixelSize: Theme.fontSmall
                }
                RowLayout {
                    Layout.alignment: Qt.AlignHCenter
                    spacing: 6
                    PillButton {
                        visible: profile.isUser && profile.chatId !== messages.chatId
                        text: qsTr("Message")
                        iconName: "reply"
                        onClicked: root.personRequested(profile.userId)
                    }
                    PillButton {
                        visible: profile.chatId !== 0 && profile.chatId === messages.chatId
                        text: qsTr("Search")
                        iconName: "search"
                        onClicked: root.searchRequested()
                    }
                    PillButton {
                        objectName: "addToContacts"
                        visible: profile.isUser && !profile.isContact
                        text: qsTr("Add contact")
                        iconName: "person"
                        onClicked: contacts.addFromProfile(profile.userId, false)
                    }
                    PillButton {
                        objectName: "profileMute"
                        visible: profile.chatId !== 0
                        text: profile.muted ? qsTr("Unmute") : qsTr("Mute")
                        iconName: profile.muted ? "bell" : "muted"
                        onClicked: chatActions.mute(profile.chatId, profile.muted ? 0 : -1)
                    }
                }

                // Editing the name and description (admins).
                ColumnLayout {
                    Layout.fillWidth: true
                    visible: root.editing
                    spacing: 6
                    TextField {
                        id: editTitle
                        Layout.fillWidth: true
                        text: profile.title
                        color: Theme.text
                        font.pixelSize: Theme.fontBody
                        padding: 8
                        background: Rectangle {
                            radius: 8
                            color: Theme.field
                            border.width: 1
                            border.color: editTitle.activeFocus ? Theme.accent : Theme.fieldBorder
                        }
                    }
                    TextArea {
                        id: editDescription
                        Layout.fillWidth: true
                        text: profile.description
                        placeholderText: qsTr("Description")
                        wrapMode: TextArea.Wrap
                        color: Theme.text
                        placeholderTextColor: Theme.textMuted
                        font.pixelSize: Theme.fontBody
                        padding: 8
                        background: Rectangle {
                            radius: 8
                            color: Theme.field
                            border.width: 1
                            border.color: editDescription.activeFocus ? Theme.accent
                                                                      : Theme.fieldBorder
                        }
                    }
                    RowLayout {
                        Item { Layout.fillWidth: true }
                        PillButton {
                            text: qsTr("Cancel")
                            onClicked: root.editing = false
                        }
                        PillButton {
                            text: qsTr("Save")
                            filled: true
                            onClicked: {
                                groupAdmin.editInfo(profile.chatId, editTitle.text,
                                                    editDescription.text)
                                root.editing = false
                            }
                        }
                    }
                }

                // Admin tools: invite link, members, edit, delete.
                ColumnLayout {
                    Layout.fillWidth: true
                    visible: root.isAdmin && !root.editing
                    spacing: 6
                    Text {
                        text: qsTr("Invite link")
                        color: Theme.textMuted
                        font.pixelSize: Theme.fontSmall
                        font.weight: Font.DemiBold
                    }
                    TextEdit {
                        objectName: "inviteLink"
                        Layout.fillWidth: true
                        readOnly: true
                        selectByMouse: true
                        wrapMode: TextEdit.WrapAnywhere
                        text: root.inviteLink !== "" ? root.inviteLink : qsTr("Loading\u2026")
                        color: Theme.accent
                        font.pixelSize: Theme.fontBody
                    }
                    Flow {
                        Layout.fillWidth: true
                        spacing: 6
                        PillButton {
                            text: qsTr("Copy")
                            iconName: "copy"
                            enabled: root.inviteLink !== ""
                            onClicked: groupAdmin.copyInviteLink(profile.chatId)
                        }
                        PillButton {
                            text: qsTr("New link")
                            iconName: "refresh"
                            onClicked: groupAdmin.resetInviteLink(profile.chatId)
                        }
                        PillButton {
                            visible: profile.members.length > 0 || root.myRole !== ""
                            text: qsTr("Add members")
                            iconName: "person"
                            onClicked: root.addMembersRequested(profile.chatId)
                        }
                        PillButton {
                            text: qsTr("Edit")
                            iconName: "edit"
                            onClicked: root.editing = true
                        }
                        PillButton {
                            visible: root.myRole === "owner"
                            text: qsTr("Delete")
                            iconName: "trash"
                            danger: true
                            onClicked: adminConfirm.ask(
                                qsTr("Delete \u201c%1\u201d for everyone?").arg(profile.title),
                                qsTr("All messages and members go. This can't be undone."),
                                qsTr("Delete"), "", profile.chatId)
                        }
                    }
                }

                Field { label: qsTr("Username"); value: profile.username ? "@" + profile.username : "" }
                Field { label: qsTr("Phone"); value: profile.phone }
                Field {
                    label: profile.isUser ? qsTr("Bio") : qsTr("Description")
                    value: profile.description
                }

                Text {
                    visible: profile.members.length > 0
                    Layout.topMargin: 6
                    text: qsTr("Members")
                    color: Theme.textMuted
                    font.pixelSize: Theme.fontSmall
                    font.weight: Font.DemiBold
                }
                Repeater {
                    model: profile.members
                    PersonRow {
                        id: memberRow
                        required property var modelData
                        title: modelData.name
                        detail: modelData.status
                        badge: modelData.role === "owner" ? qsTr("owner")
                               : modelData.role === "admin" ? qsTr("admin") : ""
                        avatar: modelData.avatar
                        initials: modelData.initials
                        colorIndex: modelData.colorIndex
                        onClicked: profile.open(0, modelData.userId)
                        TapHandler {
                            acceptedButtons: Qt.RightButton
                            enabled: root.isAdmin && memberRow.modelData.role !== "owner"
                            onTapped: {
                                root.memberMenuUser = memberRow.modelData
                                memberMenu.popup()
                            }
                        }
                    }
                }

                Text {
                    visible: profile.commonGroups.length > 0
                    Layout.topMargin: 6
                    text: qsTr("Groups in common")
                    color: Theme.textMuted
                    font.pixelSize: Theme.fontSmall
                    font.weight: Font.DemiBold
                }
                Repeater {
                    model: profile.commonGroups
                    PersonRow {
                        required property var modelData
                        title: modelData.title
                        avatar: modelData.avatar
                        initials: modelData.initials
                        colorIndex: modelData.colorIndex
                        onClicked: root.chatRequested(modelData.chatId)
                    }
                }

                // What was shared in the chat.
                Flow {  // the tabs wrap in a narrow panel
                    visible: profile.chatId !== 0
                    Layout.fillWidth: true
                    Layout.topMargin: 8
                    spacing: 4
                    Repeater {
                        model: [
                            { key: "messages", label: qsTr("Messages") },
                            { key: "media", label: qsTr("Media") },
                            { key: "files", label: qsTr("Files") },
                            { key: "links", label: qsTr("Links") },
                            { key: "voice", label: qsTr("Voice") },
                        ]
                        PillButton {
                            required property var modelData
                            text: modelData.label
                            visible: modelData.key !== "messages" || profile.isUser
                            filled: profile.tab === modelData.key
                            // Messages: the person's own messages, in the side panel; in a
                            // group, there; otherwise in the private chat.
                            onClicked: modelData.key === "messages"
                                ? root.personMessagesRequested(
                                      messages.chatType === "group" || messages.chatType === "supergroup"
                                      ? messages.chatId : profile.chatId,
                                      "user:" + profile.userId, profile.title)
                                : profile.setTab(modelData.key)
                        }
                    }
                }
                Flow {  // media: square thumbnails
                    objectName: "sharedMedia"
                    Layout.fillWidth: true
                    visible: profile.chatId !== 0 && profile.tab === "media"
                    spacing: 3
                    Repeater {
                        model: profile.tab === "media" ? profile.shared : []
                        Rectangle {
                            required property var modelData
                            width: (column.width - 6) / 3
                            height: width
                            color: Theme.pill
                            Image {
                                anchors.fill: parent
                                source: modelData.image
                                fillMode: Image.PreserveAspectCrop
                                asynchronous: true
                            }
                            Icon {
                                anchors.centerIn: parent
                                visible: modelData.kind === "video"
                                name: "play"
                                color: "#FFFFFF"
                                size: 22
                            }
                            TapHandler {
                                onTapped: root.messageRequested(modelData.chatId,
                                                                modelData.messageId)
                            }
                        }
                    }
                }
                Repeater {  // files, links, voice: a list
                    model: profile.tab !== "media" ? profile.shared : []
                    Rectangle {
                        id: sharedRow
                        required property var modelData
                        Layout.fillWidth: true
                        implicitHeight: 48
                        radius: 8
                        color: sharedHover.hovered ? Theme.hover : "transparent"
                        HoverHandler { id: sharedHover; cursorShape: Qt.PointingHandCursor }
                        TapHandler {
                            onTapped: sharedRow.modelData.url !== ""
                                      ? messages.openLink(sharedRow.modelData.url)
                                      : root.messageRequested(sharedRow.modelData.chatId,
                                                              sharedRow.modelData.messageId)
                        }
                        Rectangle {
                            x: 4
                            anchors.verticalCenter: parent.verticalCenter
                            width: 38
                            height: 38
                            radius: 8
                            color: Theme.accent
                            clip: true
                            Image {
                                anchors.fill: parent
                                visible: source !== ""
                                source: sharedRow.modelData.image
                                fillMode: Image.PreserveAspectCrop
                            }
                            Icon {
                                anchors.centerIn: parent
                                visible: sharedRow.modelData.image === ""
                                name: profile.tab === "links" ? "link"
                                      : profile.tab === "voice" ? "mic" : "file"
                                color: Theme.textOnAccent
                                size: 18
                            }
                        }
                        Column {
                            x: 50
                            width: parent.width - 54
                            anchors.verticalCenter: parent.verticalCenter
                            Text {
                                width: parent.width
                                text: sharedRow.modelData.title
                                textFormat: Text.PlainText
                                elide: Text.ElideRight
                                color: Theme.text
                                font.pixelSize: Theme.fontBody
                            }
                            Text {
                                width: parent.width
                                text: sharedRow.modelData.subtitle
                                textFormat: Text.PlainText
                                elide: Text.ElideRight
                                color: Theme.textMuted
                                font.pixelSize: Theme.fontSmall
                            }
                        }
                    }
                }
                Text {
                    Layout.fillWidth: true
                    horizontalAlignment: Text.AlignHCenter
                    visible: profile.chatId !== 0 && profile.shared.length === 0
                    text: profile.sharedBusy ? qsTr("Loading…") : qsTr("Nothing here yet")
                    color: Theme.textMuted
                    font.pixelSize: Theme.fontSmall
                }
            }
        }
    }
}
