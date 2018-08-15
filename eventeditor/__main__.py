import argparse
import gzip
import os
import signal
import sys
import traceback
import typing

import evfl
from evfl import EventFlow
from eventeditor.actor_view import ActorView
from eventeditor.event_view import EventView
from eventeditor.flow_data import FlowData
from eventeditor.flowchart_view import FlowchartView
import eventeditor.util as util
import PyQt5.QtCore as qc # type: ignore
import PyQt5.QtGui as qg # type: ignore
import PyQt5.QtWidgets as q # type: ignore

class MainWindow(q.QMainWindow):
    def __init__(self, args) -> None:
        super().__init__()
        self.args = args
        self.flow: typing.Optional[EventFlow] = None
        self.flow_data = FlowData()
        self.flow_path = ''
        self.unsaved = False

        self.initMenu()
        self.initWidgets()
        self.initLayout()

        self.connectWidgets()
        self.centralWidget().setHidden(True)
        self.updateTitleAndActions()

    def show(self) -> None:
        super().show()
        if self.args.event_flow_file:
            self.readFlow(self.args.event_flow_file)

    def initMenu(self) -> None:
        self.new_action = q.QAction('&New...', self)
        self.new_action.setShortcut(qg.QKeySequence.New)
        self.new_action.triggered.connect(self.onNewFile)

        self.open_action = q.QAction('&Open...', self)
        self.open_action.setShortcut(qg.QKeySequence.Open)
        self.open_action.triggered.connect(self.onOpenFile)

        self.open_autosave_action = q.QAction('Open autosave...', self)
        self.open_autosave_action.triggered.connect(lambda: self.onOpenFile(str(self.flow_data.auto_save.get_directory()), name_filter=f'Flowchart autosave (autosave_{self.flow_data.flow.name}_*.bfevfl.gz)'))
        if not self.flow_data.auto_save.get_directory():
            self.open_autosave_action.setVisible(False)

        self.save_action = q.QAction('&Save', self)
        self.save_action.setShortcut(qg.QKeySequence.Save)
        self.save_action.setEnabled(False)
        self.save_action.triggered.connect(self.onSaveFile)

        self.save_as_action = q.QAction('Save as...', self)
        self.save_as_action.setShortcut(qg.QKeySequence.SaveAs)
        self.save_as_action.setEnabled(False)
        self.save_as_action.triggered.connect(self.onSaveAsFile)

        self.rename_flow_action = q.QAction('Rename flow', self)
        self.rename_flow_action.triggered.connect(self.renameFlow)

        self.exit_action = q.QAction('E&xit', self)
        self.exit_action.setShortcut(qg.QKeySequence.Quit)
        self.exit_action.triggered.connect(self.close)

        menu = self.menuBar()

        file_menu = menu.addMenu('&File')
        for a in (self.new_action, self.open_action, self.open_autosave_action, self.save_action, self.save_as_action, self.rename_flow_action, self.exit_action):
            file_menu.addAction(a)

        view_menu = menu.addMenu('Flowc&hart')
        self.event_name_visible_action = q.QAction('&Show event names', self)
        self.event_name_visible_action.setCheckable(True)
        self.event_name_visible_action.setChecked(False)
        self.event_name_visible_action.triggered.connect(self.onEventNameVisibilityChanged)
        view_menu.addAction(self.event_name_visible_action)
        self.reload_graph_action = q.QAction('&Reload graph', self)
        self.reload_graph_action.setShortcut('Ctrl+Shift+R')
        view_menu.addAction(self.reload_graph_action)
        view_menu.addSeparator()
        self.add_event_action = q.QAction('&Add event...', self)
        view_menu.addAction(self.add_event_action)
        self.add_fork_action = q.QAction('Add fork...', self)
        view_menu.addAction(self.add_fork_action)

    def initWidgets(self) -> None:
        self.tab_widget = q.QTabWidget(self)
        self.tab_widget.setTabPosition(q.QTabWidget.South)

        self.flowchart_view = FlowchartView(self, self.flow_data)
        self.actor_view = ActorView(self, self.flow_data)
        self.event_view = EventView(self, self.flow_data)

    def initLayout(self) -> None:
        self.tab_widget.addTab(self.flowchart_view, 'F&lowchart')
        self.tab_widget.addTab(self.actor_view, '&Actors')
        self.tab_widget.addTab(self.event_view, '&Events')

        self.setCentralWidget(self.tab_widget)

    def connectWidgets(self) -> None:
        def set_unsaved_flag():
            self.unsaved = True
        self.flow_data.flowDataChanged.connect(set_unsaved_flag)
        self.flow_data.flowDataChanged.connect(self.updateTitleAndActions)

        self.flowchart_view.readySignal.connect(self.onViewReady)
        self.flowchart_view.eventSelected.connect(self.onEventSelected)
        self.reload_graph_action.triggered.connect(self.flowchart_view.reload)
        self.add_event_action.triggered.connect(self.flowchart_view.addNewEvent)
        self.add_fork_action.triggered.connect(self.flowchart_view.addFork)

        self.actor_view.detail_pane.jumpToEventsRequested.connect(self.onJumpToEventsRequested)
        self.actor_view.jumpToActorEventsRequested.connect(self.onJumpToEventsRequested)
        self.event_view.jumpToFlowchartRequested.connect(self.onJumpToFlowchartRequested)

        self.tab_widget.currentChanged.connect(self.onTabChanged)

    def closeEvent(self, event) -> None:
        if not self.unsaved or not self.flow:
            event.accept()
            return

        ret = q.QMessageBox.question(self, 'Unsaved changes', f'{self.flow.name} has unsaved changes. Save changes before closing?', q.QMessageBox.Yes | q.QMessageBox.No | q.QMessageBox.Cancel)

        if ret == q.QMessageBox.Yes:
            self.writeFlow(self.flow_path)
            event.accept()
        elif ret == q.QMessageBox.No:
            event.accept()
        else:
            event.ignore()

    def updateTitleAndActions(self) -> None:
        if not self.flow:
            self.setWindowTitle('EventEditor')
        else:
            indicator = '*' if self.unsaved else ''
            self.setWindowTitle(f'EventEditor - {indicator}{self.flow.name}')

        self.open_autosave_action.setEnabled(bool(self.flow) and bool(self.flow_path))
        self.add_event_action.setEnabled(bool(self.flow) and bool(self.flow_path))
        self.add_fork_action.setEnabled(bool(self.flow) and bool(self.flow_path))
        self.event_name_visible_action.setEnabled(bool(self.flow) and bool(self.flow_path))
        self.rename_flow_action.setEnabled(bool(self.flow) and bool(self.flow_path))
        self.reload_graph_action.setEnabled(bool(self.flow) and bool(self.flow_path))
        self.save_action.setEnabled(bool(self.flow) and bool(self.flow_path))
        self.save_as_action.setEnabled(bool(self.flow))

    def renameFlow(self) -> None:
        if not self.flow or not self.flow.flowchart:
            return
        text, ok = q.QInputDialog.getText(self, 'Rename', 'Enter a new name for the flowchart.', q.QLineEdit.Normal, self.flow.name)
        if not ok or not text:
            return
        self.flow.name = text
        self.flow.flowchart.name = text
        self.flow_data.flowDataChanged.emit()

    def readFlow(self, path: str) -> bool:
        if self.flow and self.unsaved:
            ret = q.QMessageBox.question(self, 'Unsaved changes', f'{self.flow.name} has unsaved changes. Save changes before opening another file?', q.QMessageBox.Yes | q.QMessageBox.No | q.QMessageBox.Cancel)
            if ret == q.QMessageBox.Yes:
                self.writeFlow(self.flow_path)
            elif ret == q.QMessageBox.Cancel:
                return False

        try:
            flow = EventFlow()
            util.read_flow(path, flow)
            self.flow = flow
            self.flow_path = path
            self.flow_data.setFlow(flow)
            self.unsaved = False
            self.updateTitleAndActions()
            return True
        except:
            traceback.print_exc()
            q.QMessageBox.critical(self, 'Open', 'Failed to load event flow')
            return False

    def writeFlow(self, path: str) -> bool:
        if not self.flow or not path:
            return False

        try:
            util.write_flow(path, self.flow)
            self.flow_path = path
            self.unsaved = False
            self.updateTitleAndActions()
            return True
        except:
            traceback.print_exc()
            q.QMessageBox.critical(self, 'Save', 'Failed to write event flow. Please ensure there are no placeholder events left.')
            return False

    def onNewFile(self) -> bool:
        path = q.QFileDialog.getSaveFileName(self, 'Select a location for the new file', '', 'Flowchart (*.bfevfl)')[0]
        if not path:
            return False
        flow = evfl.EventFlow()
        flow.name = 'NewFile'
        flow.flowchart = evfl.Flowchart()
        flow.flowchart.name = 'NewFile'
        try:
            util.write_flow(path, flow)
        except:
            traceback.print_exc()
            q.QMessageBox.critical(self, 'New file', 'Failed to write new event flow -- cannot continue')
            return False
        return self.readFlow(path)

    def onOpenFile(self, default_directory='', name_filter='Flowchart (*.bfevfl)') -> bool:
        default_directory_ = default_directory if default_directory else self.flow_path
        path = q.QFileDialog.getOpenFileName(self, 'Open event flowchart', default_directory_, name_filter)[0]
        if path:
            return self.readFlow(path)
        return False

    def onSaveFile(self) -> None:
        self.writeFlow(self.flow_path)

    def onSaveAsFile(self) -> None:
        path = q.QFileDialog.getSaveFileName(self, 'Save as...', '', 'Flowchart (*.bfevfl)')[0]
        self.writeFlow(path)

    def onTabChanged(self, idx: int) -> None:
        self.flowchart_view.setIsCurrentView(self.tab_widget.widget(idx) == self.flowchart_view)

    def onViewReady(self) -> None:
        self.centralWidget().setHidden(False)
        self.onEventNameVisibilityChanged()

    def onEventSelected(self, event_idx: int) -> None:
        self.event_view.selectEvent(event_idx)

    def onJumpToEventsRequested(self, filter_str: str = '') -> None:
        self.tab_widget.setCurrentWidget(self.event_view)
        if filter_str:
            self.event_view.search_bar.setValue(filter_str)
            self.event_view.search_bar.show()

    def onJumpToFlowchartRequested(self, idx: int) -> None:
        """Request a node select in the flowchart webview. Negative indices are used for entry points."""
        self.tab_widget.setCurrentWidget(self.flowchart_view)
        self.flowchart_view.selectRequested.emit(idx)

    def onEventNameVisibilityChanged(self) -> None:
        visible = self.event_name_visible_action.isChecked()
        self.flowchart_view.eventNameVisibilityChanged.emit(visible)

def main() -> None:
    signal.signal(signal.SIGINT, signal.SIG_DFL)

    parser = argparse.ArgumentParser(prog='eventeditor', description='An event editor for Breath of the Wild')
    parser.add_argument('event_flow_file', nargs='?', help='Event flow file to open')
    args, _ = parser.parse_known_args()
    app = q.QApplication(sys.argv)
    app.setApplicationName('eventeditor')
    if os.name == 'nt':
        app_font = app.font()
        app_font.setFamily('Segoe UI')
        app_font.setPointSize(int(qg.QFontInfo(app_font).pointSize() * 1.20))
        app.setFont(app_font)
    win = MainWindow(args)
    win.show()
    ret = app.exec_()
    sys.exit(ret)

if __name__ == '__main__':
    main()