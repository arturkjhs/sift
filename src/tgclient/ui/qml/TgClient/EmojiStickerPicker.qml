import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts
import QtQuick.Window

// Emoji and sticker picker above the composer. An emoji goes into the text (the picker stays
// open for more); a sticker is sent right away.
Popup {
    id: root
    objectName: "emojiPicker"

    property string tab: "emoji"

    signal emojiPicked(string emoji)
    signal stickerPicked(var sticker)
    signal gifPicked(var animation)

    width: 372
    height: 430
    padding: 0
    focus: true
    closePolicy: Popup.CloseOnEscape | Popup.CloseOnPressOutsideParent

    onOpened: {
        emojis.refresh()
        stickers.load()
        if (tab === "emoji")
            emojiSearch.focusInput()
    }
    onClosed: emojiSearch.clear()

    enter: Transition {
        NumberAnimation { property: "opacity"; from: 0; to: 1; duration: 110 }
        NumberAnimation { property: "scale"; from: 0.97; to: 1; duration: 140; easing.type: Easing.OutCubic }
    }
    exit: Transition {
        NumberAnimation { property: "opacity"; to: 0; duration: 80 }
    }

    background: Item {
        Repeater {
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

    // One square tab button: a glyph (emoji), an icon, or an image (sticker set cover).
    component PickerTab: AbstractButton {
        id: tabButton
        property string glyph: ""
        property string iconName: ""
        property string imageSource: ""
        property bool selected: false

        implicitWidth: 34
        implicitHeight: 34
        hoverEnabled: true
        ToolTip.visible: hovered && text !== ""
        ToolTip.text: text
        ToolTip.delay: 600

        background: Rectangle {
            radius: 8
            color: tabButton.selected ? Theme.selection
                 : tabButton.hovered ? Theme.hover : "transparent"
        }
        contentItem: Item {
            Text {
                anchors.centerIn: parent
                visible: tabButton.glyph !== ""
                text: tabButton.glyph
                font.pixelSize: 19
            }
            Icon {
                anchors.centerIn: parent
                name: tabButton.iconName
                color: tabButton.selected ? Theme.accent : Theme.textMuted
                size: 18
            }
            Image {
                anchors.centerIn: parent
                visible: tabButton.imageSource !== ""
                width: 26
                height: 26
                source: tabButton.imageSource
                sourceSize.width: Math.ceil(26 * Screen.devicePixelRatio)
                sourceSize.height: Math.ceil(26 * Screen.devicePixelRatio)
                fillMode: Image.PreserveAspectFit
                asynchronous: true
            }
        }
    }

    contentItem: ColumnLayout {
        spacing: 0

        RowLayout {
            Layout.fillWidth: true
            Layout.margins: 10
            Layout.bottomMargin: 6
            spacing: 6

            PillButton {
                objectName: "emojiTab"
                text: qsTr("Emoji")
                iconName: "emoji"
                filled: root.tab === "emoji"
                onClicked: {
                    root.tab = "emoji"
                    emojiSearch.focusInput()
                }
            }
            PillButton {
                objectName: "stickersTab"
                text: qsTr("Stickers")
                iconName: "sticker"
                filled: root.tab === "stickers"
                onClicked: root.tab = "stickers"
            }
            PillButton {
                objectName: "gifsTab"
                text: qsTr("GIFs")
                filled: root.tab === "gifs"
                onClicked: {
                    root.tab = "gifs"
                    gifs.load()
                    gifSearch.focusInput()
                }
            }
            Item { Layout.fillWidth: true }
        }

        StackLayout {
            Layout.fillWidth: true
            Layout.fillHeight: true
            currentIndex: root.tab === "emoji" ? 0 : root.tab === "stickers" ? 1 : 2

            // --- emoji ------------------------------------------------------------------------
            ColumnLayout {
                spacing: 6

                SearchBox {
                    id: emojiSearch
                    Layout.fillWidth: true
                    Layout.leftMargin: 10
                    Layout.rightMargin: 10
                    placeholder: qsTr("Search emoji")
                    onEdited: text => emojis.query = text
                    onCleared: emojis.query = ""
                }

                ListView {
                    id: categoryBar
                    Layout.fillWidth: true
                    Layout.leftMargin: 6
                    Layout.rightMargin: 6
                    implicitHeight: 34
                    visible: emojis.query.trim() === ""
                    orientation: ListView.Horizontal
                    spacing: 2
                    clip: true
                    boundsBehavior: Flickable.StopAtBounds
                    model: emojis.categories
                    delegate: PickerTab {
                        required property var modelData
                        text: modelData.title
                        glyph: modelData.glyph
                        iconName: modelData.glyph === "" ? "clock" : ""
                        selected: emojis.category === modelData.key
                        onClicked: {
                            emojis.category = modelData.key
                            emojiGrid.positionViewAtBeginning()
                        }
                    }
                }

                GridView {
                    id: emojiGrid
                    objectName: "emojiGrid"
                    Layout.fillWidth: true
                    Layout.fillHeight: true
                    Layout.leftMargin: 6
                    Layout.rightMargin: 6
                    Layout.bottomMargin: 6
                    clip: true
                    boundsBehavior: Flickable.StopAtBounds
                    cellWidth: Math.floor(width / 8)
                    cellHeight: cellWidth
                    model: emojis
                    ScrollBar.vertical: ScrollBar {}

                    delegate: AbstractButton {
                        id: emojiCell
                        required property string emoji
                        required property string name
                        width: emojiGrid.cellWidth
                        height: emojiGrid.cellHeight
                        hoverEnabled: true
                        Accessible.name: name
                        ToolTip.visible: hovered
                        ToolTip.text: name
                        ToolTip.delay: 900

                        background: Rectangle {
                            radius: 8
                            color: emojiCell.down ? Theme.selection
                                 : emojiCell.hovered ? Theme.hover : "transparent"
                        }
                        contentItem: Text {
                            text: emojiCell.emoji
                            horizontalAlignment: Text.AlignHCenter
                            verticalAlignment: Text.AlignVCenter
                            font.pixelSize: 25
                        }
                        onClicked: {
                            emojis.use(emoji)
                            root.emojiPicked(emoji)
                        }
                    }

                    Text {
                        anchors.centerIn: parent
                        width: parent.width - 40
                        visible: emojiGrid.count === 0
                        horizontalAlignment: Text.AlignHCenter
                        wrapMode: Text.Wrap
                        text: emojis.query.trim() !== "" ? qsTr("No emoji found")
                                                         : qsTr("Emoji you use will appear here")
                        color: Theme.textMuted
                        font.pixelSize: Theme.fontBody
                    }
                }
            }

            // --- stickers -----------------------------------------------------------------------
            ColumnLayout {
                spacing: 6

                ListView {
                    id: setBar
                    objectName: "stickerSets"
                    Layout.fillWidth: true
                    Layout.leftMargin: 6
                    Layout.rightMargin: 6
                    implicitHeight: 38
                    orientation: ListView.Horizontal
                    spacing: 2
                    clip: true
                    boundsBehavior: Flickable.StopAtBounds
                    model: stickers.sets
                    delegate: PickerTab {
                        required property var modelData
                        implicitWidth: 38
                        implicitHeight: 38
                        text: modelData.title
                        iconName: modelData.key === "recent" ? "clock" : ""
                        imageSource: modelData.source
                        glyph: modelData.key !== "recent" && modelData.source === ""
                               ? modelData.title.charAt(0) : ""
                        selected: stickers.currentSet === modelData.key
                        onClicked: {
                            stickers.currentSet = modelData.key
                            stickerGrid.positionViewAtBeginning()
                        }
                    }
                }

                Rectangle {
                    Layout.fillWidth: true
                    implicitHeight: 1
                    color: Theme.separator
                }

                GridView {
                    id: stickerGrid
                    objectName: "stickerGrid"
                    Layout.fillWidth: true
                    Layout.fillHeight: true
                    Layout.leftMargin: 6
                    Layout.rightMargin: 6
                    Layout.bottomMargin: 6
                    clip: true
                    boundsBehavior: Flickable.StopAtBounds
                    cellWidth: Math.floor(width / 4)
                    cellHeight: cellWidth
                    model: stickers
                    ScrollBar.vertical: ScrollBar {}

                    delegate: AbstractButton {
                        id: stickerCell
                        required property int index
                        required property string source
                        required property string emoji
                        width: stickerGrid.cellWidth
                        height: stickerGrid.cellHeight
                        hoverEnabled: true
                        Accessible.name: emoji

                        background: Rectangle {
                            radius: 10
                            color: stickerCell.hovered ? Theme.hover : "transparent"
                        }
                        contentItem: Item {
                            Text {  // until the preview is downloaded
                                anchors.centerIn: parent
                                visible: picture.status !== Image.Ready
                                text: stickerCell.emoji
                                font.pixelSize: 28
                                opacity: 0.5
                            }
                            Image {
                                id: picture
                                anchors.fill: parent
                                anchors.margins: 6
                                source: stickerCell.source
                                sourceSize.width: Math.ceil(width * Screen.devicePixelRatio)
                                sourceSize.height: Math.ceil(height * Screen.devicePixelRatio)
                                fillMode: Image.PreserveAspectFit
                                asynchronous: true
                                scale: stickerCell.hovered ? 1.06 : 1
                                Behavior on scale { NumberAnimation { duration: 100 } }
                            }
                        }
                        onClicked: root.stickerPicked(stickers.sticker(index))
                    }

                    Text {
                        anchors.centerIn: parent
                        width: parent.width - 40
                        visible: stickerGrid.count === 0
                        horizontalAlignment: Text.AlignHCenter
                        wrapMode: Text.Wrap
                        text: stickers.loading ? qsTr("Loading stickers…")
                              : setBar.count === 0
                                ? qsTr("No sticker sets yet. Add some in Telegram on your phone.")
                                : qsTr("No stickers here")
                        color: Theme.textMuted
                        font.pixelSize: Theme.fontBody
                    }
                }
            }

            // --- GIFs: saved ones, or found by the @gif bot ------------------------------------
            ColumnLayout {
                spacing: 6

                SearchBox {
                    id: gifSearch
                    objectName: "gifSearch"
                    Layout.fillWidth: true
                    Layout.leftMargin: 10
                    Layout.rightMargin: 10
                    placeholder: qsTr("Search GIFs")
                    onEdited: text => gifs.query = text
                    onCleared: gifs.query = ""
                }

                GridView {
                    id: gifGrid
                    objectName: "gifGrid"
                    Layout.fillWidth: true
                    Layout.fillHeight: true
                    Layout.leftMargin: 6
                    Layout.rightMargin: 6
                    Layout.bottomMargin: 6
                    clip: true
                    boundsBehavior: Flickable.StopAtBounds
                    cellWidth: Math.floor(width / 3)
                    cellHeight: Math.floor(cellWidth * 0.75)
                    model: gifs
                    ScrollBar.vertical: ScrollBar {}

                    delegate: AbstractButton {
                        id: gifCell
                        required property int index
                        required property string source
                        width: gifGrid.cellWidth
                        height: gifGrid.cellHeight
                        hoverEnabled: true
                        padding: 2
                        background: Rectangle {
                            radius: 6
                            color: gifCell.hovered ? Theme.hover : Theme.pill
                        }
                        contentItem: Image {
                            source: gifCell.source
                            fillMode: Image.PreserveAspectCrop
                            asynchronous: true
                            clip: true
                        }
                        onClicked: root.gifPicked(gifs.animation(index))
                    }

                    Text {
                        anchors.centerIn: parent
                        width: parent.width - 40
                        visible: gifGrid.count === 0
                        horizontalAlignment: Text.AlignHCenter
                        wrapMode: Text.Wrap
                        text: gifs.loading ? qsTr("Searching\u2026")
                              : gifs.query !== "" ? qsTr("No GIFs found")
                              : qsTr("GIFs you send will appear here. Search to find more.")
                        color: Theme.textMuted
                        font.pixelSize: Theme.fontBody
                    }
                }
            }
        }
    }
}
