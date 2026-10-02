import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts

// The current account's avatar; click: switch accounts, add one, log out.
AbstractButton {
    id: root
    objectName: "accountSwitcher"

    readonly property var current: {
        const list = accounts.accounts
        for (let i = 0; i < list.length; ++i)
            if (list[i].active)
                return list[i]
        return { name: "", avatar: "", initials: "?", colorIndex: 0 }
    }
    readonly property int otherUnread: {
        let total = 0
        for (const account of accounts.accounts)
            if (!account.active)
                total += account.unread
        return total
    }

    implicitWidth: 34
    implicitHeight: 34
    hoverEnabled: true
    Accessible.name: qsTr("Accounts")
    ToolTip.visible: hovered
    ToolTip.delay: 600
    ToolTip.text: root.current.name
    onClicked: menu.opened ? menu.close() : menu.open()

    contentItem: Item {
        Avatar {
            anchors.centerIn: parent
            size: 30
            avatarSource: root.current.avatar
            initials: root.current.initials
            colorIndex: root.current.colorIndex
        }
        Rectangle {  // unread in the other accounts
            visible: root.otherUnread > 0
            x: parent.width - width + 2
            y: -2
            width: 12
            height: 12
            radius: 6
            color: Theme.accent
            border.width: 2
            border.color: Theme.sidebar
        }
    }
    background: Item {}

    Popup {
        id: menu
        objectName: "accountMenu"
        y: root.height + 6
        padding: 5
        width: 260

        background: Rectangle {
            radius: 10
            color: Theme.popup
            border.width: 1
            border.color: Theme.popupBorder
        }

        contentItem: ColumnLayout {
            spacing: 0

            Repeater {
                model: accounts.accounts
                AbstractButton {
                    id: row
                    required property var modelData
                    Layout.fillWidth: true
                    implicitHeight: 44
                    hoverEnabled: true
                    onClicked: {
                        menu.close()
                        accounts.switchTo(modelData.key)
                    }
                    background: Rectangle {
                        radius: 6
                        color: row.hovered ? Theme.hover : "transparent"
                    }
                    contentItem: RowLayout {
                        spacing: 10
                        Avatar {
                            Layout.leftMargin: 6
                            size: 30
                            avatarSource: row.modelData.avatar
                            initials: row.modelData.initials
                            colorIndex: row.modelData.colorIndex
                        }
                        Text {
                            Layout.fillWidth: true
                            text: row.modelData.name
                            textFormat: Text.PlainText
                            elide: Text.ElideRight
                            color: Theme.text
                            font.pixelSize: Theme.fontBody
                            font.weight: row.modelData.active ? Font.DemiBold : Font.Normal
                        }
                        Rectangle {
                            visible: row.modelData.unread > 0 && !row.modelData.active
                            implicitHeight: 20
                            implicitWidth: Math.max(20, unreadText.implicitWidth + 12)
                            radius: 10
                            color: Theme.accent
                            Text {
                                id: unreadText
                                anchors.centerIn: parent
                                text: row.modelData.unread
                                color: Theme.textOnAccent
                                font.pixelSize: 11
                                font.weight: Font.Bold
                            }
                        }
                        Text {
                            Layout.rightMargin: 8
                            visible: row.modelData.active
                            text: "✓"
                            color: Theme.accent
                            font.pixelSize: Theme.fontTitle
                        }
                    }
                }
            }

            AppMenuSeparator { Layout.fillWidth: true }
            AppMenuItem {
                Layout.fillWidth: true
                text: qsTr("Add account")
                iconName: "person"
                enabled: accounts.canAdd
                onClicked: {
                    menu.close()
                    accounts.addAccount()
                }
            }
            AppMenuItem {
                Layout.fillWidth: true
                text: qsTr("Log out")
                iconName: "open"
                danger: true
                onClicked: {
                    menu.close()
                    logoutDialog.open()
                }
            }
        }
    }

    Popup {
        id: logoutDialog
        objectName: "logoutDialog"
        parent: Overlay.overlay
        anchors.centerIn: parent
        width: Math.min(380, parent ? parent.width - 48 : 380)
        modal: true
        padding: 20
        background: Rectangle {
            radius: 12
            color: Theme.sidebar
            border.width: 1
            border.color: Theme.separator
        }
        contentItem: ColumnLayout {
            spacing: 14
            Text {
                Layout.fillWidth: true
                text: qsTr("Log out of %1?").arg(root.current.name)
                textFormat: Text.PlainText
                wrapMode: Text.Wrap
                color: Theme.text
                font.pixelSize: 15
                font.weight: Font.DemiBold
            }
            Text {
                Layout.fillWidth: true
                wrapMode: Text.Wrap
                text: qsTr("Downloaded files, the search index, transcripts and AI results of "
                           + "this account are deleted from this computer.")
                color: Theme.textMuted
                font.pixelSize: Theme.fontBody
            }
            RowLayout {
                Layout.fillWidth: true
                spacing: 8
                Item { Layout.fillWidth: true }
                PillButton {
                    text: qsTr("Cancel")
                    onClicked: logoutDialog.close()
                }
                PillButton {
                    text: qsTr("Log out")
                    filled: true
                    danger: true
                    onClicked: {
                        logoutDialog.close()
                        accounts.logOut()
                    }
                }
            }
        }
    }
}
