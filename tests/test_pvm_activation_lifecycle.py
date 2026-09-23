"""Exercise real Qt gestures in a child process so native failures are reported."""
from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys
import textwrap


ROOT = Path(__file__).resolve().parents[1]


def test_pvm_double_click_and_context_menu_release_native_resources(tmp_path):
    script = textwrap.dedent(r'''
        import gc
        import sys
        from pathlib import Path
        from PySide6.QtCore import QCoreApplication, QEvent, Qt, QTimer
        from PySide6.QtGui import QContextMenuEvent
        from PySide6.QtTest import QTest
        from PySide6.QtWidgets import QApplication, QMenu, QWidget
        import shiboken6
        from azeo_control_trainer.core.hmi.pvms.publishing import DisplayStore
        from azeo_control_trainer.core.hmi.pvms.rendering.items import PvmItem
        from azeo_control_trainer.core.hmi.pvms.rendering.renderer import pvm_from_dict
        from azeo_control_trainer.azeo_graphics_designer.studio.assembler import PvmStudio
        from azeo_control_trainer.core.presentation import headless
        from azeo_control_trainer.core.presentation.application_style import apply_application_style
        from azeo_control_trainer.core.hmi.theme.fonts import apply_application_font
        from azeo_control_trainer.core.presentation.menu_style import studio_menu, retain_menu
        from create_distillation_display import plant_document

        app = QApplication([])
        app.setQuitOnLastWindowClosed(False)
        apply_application_style(app)
        apply_application_font()
        headless.is_headless = lambda: False
        errors = []
        def record_error(kind, value, tb):
            errors.append(str(value))
            sys.__excepthook__(kind, value, tb)
        sys.excepthook = record_error
        root = Path(sys.argv[1])
        doc = plant_document()
        valve_ids = []
        for index, (ident, class_key, variant, path) in enumerate((
            ('valve_op', 'AO/dynamo_inline', 'valve', 'TEST/VALVE'),
            ('valve_unconfigured', 'AO/dynamo_inline', 'valve', ''),
            ('valve_device', 'DEVCTL/dynamo_compact', 'hp_b_valve', 'TEST/MOV'),
        )):
            doc.pvms.append(pvm_from_dict(dict(
                id=ident, **{'class': class_key}, variant=variant, params={'path': path},
                x=100+index*180, y=doc.height+20, w=96, h=112,
            )))
            valve_ids.append(ident)
        doc.height += 160
        DisplayStore(root).save_draft(doc)
        studio = PvmStudio(lambda: {}, root, display_name=doc.name)
        studio.uiError.connect(errors.append)
        activations = []
        open_faceplate = studio.open_faceplate
        def activate(pvm):
            activations.append(pvm.id)
            open_faceplate(pvm)
        studio.open_faceplate = activate
        studio.resize(1450, 850)
        studio.show()
        studio.enter_edit()
        studio.fit_drawing()
        QTest.qWait(100)
        for cycle in range(2):
            if cycle == 1:
                studio.leave_edit()
            for ident in ('pressure_fan', 'column', 'reflux_pump', 'pressure_loop', *valve_ids):
                item = next(i for i in studio.canvas.scene().items()
                            if isinstance(i, PvmItem) and i.pvm.id == ident)
                pos = studio.canvas.mapFromScene(item.mapToScene(item.rect().center()))
                print(cycle, ident, 'double-click', flush=True)
                before = len(activations)
                QTest.mouseClick(studio.canvas.viewport(), Qt.LeftButton, pos=pos)
                QTest.mouseDClick(studio.canvas.viewport(), Qt.LeftButton, pos=pos)
                QTest.mouseRelease(studio.canvas.viewport(), Qt.LeftButton, pos=pos)
                QTest.qWait(25)
                assert activations[before:] == [ident], activations[before:]
                faces = [w for w in studio._faceplates.values() if w.bound]
                assert len(faces) == 1 and faces[0].isVisible(), (ident, len(faces), errors)
                first = faces[0]
                assert first.params == dict(item.pvm.params), (ident, first.params)
                studio.open_faceplate(item.pvm)
                assert [w for w in studio._faceplates.values() if w.bound] == [first]
                first.close()
                assert not first.bound
                QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
                gc.collect()

                chosen = []
                def choose():
                    menu = studio._context_menu
                    action = next(a for a in menu.actions() if a.text().startswith('Open faceplate'))
                    chosen.append(action.text())
                    QTest.mouseClick(menu, Qt.LeftButton, pos=menu.actionGeometry(action).center())
                def escape_stuck_menu():
                    menu = studio._context_menu
                    if menu is not None and shiboken6.isValid(menu):
                        menu.close()
                timer = QTimer()
                timer.setSingleShot(True)
                timer.timeout.connect(escape_stuck_menu)
                timer.start(1500)
                QTimer.singleShot(100, choose)
                print(cycle, ident, 'context menu', flush=True)
                event = QContextMenuEvent(QContextMenuEvent.Mouse, pos,
                                         studio.canvas.viewport().mapToGlobal(pos))
                QApplication.sendEvent(studio.canvas.viewport(), event)
                timer.stop()
                QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
                assert chosen and not errors, (chosen, errors)
                assert studio._context_menu is None
                faces = [w for w in studio._faceplates.values() if w.bound]
                assert len(faces) == 1 and faces[0].isVisible()
                faces[0].close()
        assert not studio.findChildren(QMenu), 'Transient menus accumulated'

        # Selecting, dismissing and reusable ribbon popups have different
        # lifetime contracts; the fix must preserve all three.
        owner = QWidget()
        for select in (False, True):
            menu = studio_menu('Menu lifecycle')
            retain_menu(owner, menu)
            assert menu.windowType() == Qt.Popup
            action = menu.addAction('Open faceplate')
            def finish():
                if select:
                    QTest.mouseClick(menu, Qt.LeftButton, pos=menu.actionGeometry(action).center())
                else:
                    menu.close()
            QTimer.singleShot(50, finish)
            answer = menu.exec_transient(studio.mapToGlobal(studio.rect().center()))
            assert answer is action if select else answer is None
            QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
            assert owner._context_menu is None and not shiboken6.isValid(menu)
        reusable = studio_menu(parent=owner)
        reusable.addAction('Reusable action')
        for _ in range(2):
            reusable.popup(studio.mapToGlobal(studio.rect().center()))
            QTest.qWait(25)
            reusable.close()
            QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
            assert shiboken6.isValid(reusable)
        owner.deleteLater()
        studio.unsaved = False
        studio.close()
        studio.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
        gc.collect()
        assert not errors, errors
        print('PVM_ACTIVATION_PASS', flush=True)
    ''')
    result = subprocess.run(
        [sys.executable, '-u', '-X', 'faulthandler', '-c', script, str(tmp_path)],
        cwd=ROOT, capture_output=True, text=True, encoding='utf-8', errors='replace',
        timeout=90, env={**os.environ,
                        'PYTHONPATH': os.pathsep.join((str(ROOT / 'src'), str(ROOT / 'tools'))),
                        'QT_QPA_PLATFORM': os.environ.get('AZEO_TEST_QPA', 'offscreen')},
    )
    assert result.returncode == 0, result.stdout + result.stderr[-6000:]
    assert 'PVM_ACTIVATION_PASS' in result.stdout
