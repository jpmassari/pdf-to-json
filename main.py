import sys
import logging
import time
import base64
from pathlib import Path
import json
import requests
from functools import partial
from PySide6.QtWidgets import (
    QApplication, QWidget, QVBoxLayout,
    QPushButton, QTextEdit, QFileDialog,
    QMessageBox, QLabel, QTableWidget,
    QTableWidgetItem, QDialog, QHBoxLayout,
    QButtonGroup, QFormLayout, QLineEdit,
    QFrame)
from PySide6.QtGui import QPixmap
from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QScrollArea

from docling_core.types.doc import ImageRefMode, PictureItem, TableItem, TextItem

from docling.datamodel.base_models import FigureElement, InputFormat, Table
from docling.datamodel.pipeline_options import (PdfPipelineOptions, AcceleratorDevice, AcceleratorOptions)
from docling.document_converter import DocumentConverter, PdfFormatOption

from PIL.ImageQt import ImageQt  # Add this import at the top
from io import BytesIO
from PIL import Image

_log = logging.getLogger(__name__)

IMAGE_RESOLUTION_SCALE = 2.0
headers = {
    "Content-Type": "application/json"
}
url = "http://127.0.0.1:8000/questions"

# --- Improvement 1: Reusable clickable component --- #
class ClickableLabel(QLabel):
    clicked = Signal()  # define a signal to be emitted when the label is clicked

    def __init__(self, parent=None):
        super().__init__(parent)

    def mousePressEvent(self, event):
        self.clicked.emit()  # emit signal when clicked
        # Optionally, call the base class method if needed
        super().mousePressEvent(event)

class PDFtoJSONApp(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("PDF to JSON Converter")
        self.setGeometry(100, 100, 800, 600)  # Larger initial size

        self.element_counter = 0
        # question structure
        self.question_data = {'data': [], 'filter': {
            'materia': '',
            'assunto': '',
            'subAssunto': '',
            'faculdade': '',
            'ano': ''
        }}

        # Main split layout
        self.main_layout = QHBoxLayout(self)
        
        # Left panel (original content)
        self.left_panel = QWidget()
        self.left_layout = QVBoxLayout(self.left_panel)
        
        # Right panel (table preview)
        self.right_panel = QWidget()
        self.right_layout = QVBoxLayout(self.right_panel)
        self.right_layout.setAlignment(Qt.AlignTop)
        #self.right_panel.setFixedWidth(400)  # Fixed width for preview
        
        # Configure main layout
        self.main_layout.addWidget(self.left_panel, 2)  # 2/3 width
        self.main_layout.addWidget(self.right_panel, 1)  # 1/3 width

        # Left panel components
        self.upload_button = QPushButton("Upload PDF")
        self.upload_button.clicked.connect(self.upload_pdf)
        self.left_layout.addWidget(self.upload_button)

        # Scroll area for left content
        self.left_scroll = QScrollArea()
        self.left_scroll.setWidgetResizable(True)
        self.left_content = QWidget()
        self.left_content_layout = QVBoxLayout(self.left_content)
        self.left_scroll.setWidget(self.left_content)
        self.left_layout.addWidget(self.left_scroll)

        # --- New Right Panel Structure ---
        # Create an input area widget for fixed controls
        self.input_widget = QWidget()
        input_layout = QVBoxLayout(self.input_widget)
        # --- Matéria Input ---
        mat_label = QLabel("Matéria")
        input_layout.addWidget(mat_label)
        subject_layout = QHBoxLayout()
        self.mat_button_group = QButtonGroup(self)  # for exclusive selection
        self.mat_button_group.setExclusive(False)
        self.selected_subjects = []  # store the currently selected subject

        subjects = ["Portugues", "Matematica", "Fisica", "Quimica", "Biologia",
                    "Ingles", "Historia", "Geografia", "Filosofia", "Sociologia"]
        for subj in subjects:
            btn = QPushButton(subj)
            btn.setCheckable(True)
            # When clicked, call self.set_subject with the subject string
            btn.clicked.connect(lambda checked, s=subj, b=btn: self.update_subjects(s, b.isChecked()))
            subject_layout.addWidget(btn)
            self.mat_button_group.addButton(btn)
        input_layout.addLayout(subject_layout)
        
        # --- Other Inputs: Assunto, Faculdade, Ano ---
        form_layout = QFormLayout()
        self.assunto_edit = QLineEdit()
        self.sub_assunto_edit = QLineEdit()
        self.faculdade_edit = QLineEdit()
        self.ano_edit = QLineEdit()
        form_layout.addRow("Assunto:", self.assunto_edit)
        form_layout.addRow("Sub assunto:", self.sub_assunto_edit)
        form_layout.addRow("Faculdade:", self.faculdade_edit)
        form_layout.addRow("Ano:", self.ano_edit)
        input_layout.addLayout(form_layout)
        
        # Add the input area to the right panel first
        self.right_layout.addWidget(self.input_widget)
        
        # Add extra spacing between inputs and separator
        self.right_layout.addSpacing(10)

        # Create a horizontal separator line
        separator = QFrame()
        separator.setFrameShape(QFrame.HLine)
        separator.setLineWidth(1)
        separator.setMidLineWidth(1)
        self.right_layout.addWidget(separator)

        # Add spacing after the separator, before the previews
        self.right_layout.addSpacing(10)

        # Create a container for dynamic previews
        self.previews_widget = QWidget()
        self.previews_layout = QVBoxLayout(self.previews_widget)
        self.previews_layout.setAlignment(Qt.AlignTop)
        self.right_layout.addWidget(self.previews_widget)

        # --- Right Panel: Confirm Button ---
        self.confirm_button = QPushButton("Confirm")
        self.confirm_button.clicked.connect(self.confirm_inputs)
        # Add spacing above the confirm button if desired
        self.right_layout.addSpacing(10)
        self.right_layout.addWidget(self.confirm_button)

        #  --- Add Image Button at the Bottom ---
        self.add_image_button = QPushButton("Add Image")
        self.add_image_button.clicked.connect(self.add_image)
        self.right_layout.addSpacing(10)
        self.right_layout.addWidget(self.add_image_button)
        
        self.clear_button = QPushButton("Clear")
        self.clear_button.clicked.connect(self.clear_right_panel)
        self.right_layout.addSpacing(10)
        self.right_layout.addWidget(self.clear_button)

        # Right panel components
        #self.preview_label = QLabel("Click a table to preview")
        #self.preview_label.setAlignment(Qt.AlignCenter)
        #self.right_layout.addWidget(self.preview_label)

        # Initialize other components
        self.file_dialog = QFileDialog()
        self.pipeline_options = PdfPipelineOptions()

    def add_image(self):
        """
        Open a file dialog to allow the user to select an image file.
        Once selected, display the image in the right panel's preview area.
        """
        # Open file dialog; filter to image files (PNG, JPEG, etc.)
        file_path, _ = self.file_dialog.getOpenFileName(
            self, "Select Image", "", "Image Files (*.png *.jpg *.jpeg *.bmp)"
        )
        if file_path:
            try:
                # Load the image into a QPixmap
                pixmap = QPixmap(file_path)
                if pixmap.isNull():
                    raise Exception("Failed to load image. The file may be corrupted or in an unsupported format.")
                # Create a clickable label with the image
                image_label = ClickableLabel()
                image_label.setProperty("element_id", self.element_counter)
                image_label.setPixmap(pixmap)
                image_label.setAlignment(Qt.AlignCenter)
                # Store full pixmap for scaling/preview as in other cases
                image_label.full_pixmap = pixmap
                # Optionally, connect the clicked signal to clear the preview or open a full view
                image_label.clicked.connect(lambda: image_label.clear())
                # Add the image label to the previews layout
                self.previews_layout.addWidget(image_label)
            except Exception as e:
                QMessageBox.warning(self, "Image Error", f"Failed to add image:\n{str(e)}")


    def update_subjects(self, subject, checked):
        """Update the list of selected subjects based on button state."""
        if checked:
            if subject not in self.selected_subjects:
                self.selected_subjects.append(subject)
        else:
            if subject in self.selected_subjects:
                self.selected_subjects.remove(subject)
        print("Selected subjects:", self.selected_subjects)

    def confirm_inputs(self):
        """Function called when the Confirm button is clicked."""
        assunto = self.assunto_edit.text()
        sub_assunto = self.sub_assunto_edit.text()
        faculdade = self.faculdade_edit.text()
        ano = self.ano_edit.text()
        self.question_data['filter']['materia'] = self.selected_subjects
        self.question_data['filter']['assunto'] = [assunto]
        self.question_data['filter']['subAssunto'] = [sub_assunto]
        self.question_data['filter']['faculdade'] = faculdade
        self.question_data['filter']['ano'] = ano

        response = requests.post(url=url, headers=headers, data=json.dumps(self.question_data, ensure_ascii=False))
        
        print(response.json())

    def clear_layout(self):
        while self.left_content_layout.count():
            item = self.left_content_layout.takeAt(0)
            widget = item.widget()
            if widget:
                widget.deleteLater()
        self.clear_right_panel()

    def clear_right_panel(self):
        self.element_counter = 0
        while self.previews_layout.count():
            item = self.previews_layout.takeAt(0)
            widget = item.widget()
            if widget:
                widget.deleteLater()

        for button in self.mat_button_group.buttons():
            button.setChecked(False)
        self.question_data = {'data': [], 'filter': {
            'materia': '',
            'assunto': '',
            'subAssunto': '',
            'faculdade': '',
            'ano': ''
        }}
        self.selected_subjects = []
        self.assunto_edit.clear()
        self.sub_assunto_edit.clear()
        self.faculdade_edit.clear()
        self.ano_edit.clear()

    def upload_pdf(self):
        output_dir = Path("scratch")
        self.pipeline_options.images_scale = IMAGE_RESOLUTION_SCALE
        self.pipeline_options.do_ocr = True
        self.pipeline_options.do_table_structure = True
        self.pipeline_options.table_structure_options.do_cell_matching = True
        self.pipeline_options.generate_page_images = True
        self.pipeline_options.generate_picture_images = True
        file_path, _ = self.file_dialog.getOpenFileName(self, "Open PDF File", "", "PDF Files (*.pdf)")

        doc_converter = DocumentConverter(
            format_options={
                InputFormat.PDF: PdfFormatOption(pipeline_options=self.pipeline_options)
            }
        )

        if file_path:
            try:
                start_time = time.time()
                conv_res = doc_converter.convert(file_path)
                
                output_dir.mkdir(parents=True, exist_ok=True)
                doc_filename = conv_res.input.file.stem
                
                 # Clear previous content
                self.clear_layout()
                
                # Process elements with individual error handling
                for element, _level in conv_res.document.iterate_items():
                    try:
                        if isinstance(element, TableItem):
                            self.process_table(element, conv_res.document, output_dir, doc_filename)
                        elif isinstance(element, PictureItem):
                            self.process_picture(element, conv_res.document, output_dir, doc_filename)
                        elif isinstance(element, TextItem):
                            self.process_text(element)
                    except Exception as e:
                        error_msg = f"Error processing element {type(element).__name__}: {str(e)}"
                        print(error_msg)
                        QMessageBox.warning(self, "Processing Warning", error_msg)

                # Add spacer to push content to top
                self.left_content_layout.addStretch()
                
                # Save converted pdf
                #md_filename = output_dir / f"{doc_filename}-with-images.md"
                #conv_res.document.save_as_html(md_filename, image_mode=ImageRefMode.EMBEDDED)
                
                end_time = time.time() - start_time
                _log.info(f"Document processed in {end_time:.2f} seconds.")
                
            except Exception as e:
                QMessageBox.critical(self, "Critical Error", f"Failed to process PDF:\n{e}")

    def process_table(self, element, document, output_dir, doc_filename):
        try:
            table_counter = len([w for w in self.left_content.findChildren(QLabel) if "table" in w.objectName()]) + 1
            image = element.get_image(document)
            
            # Encode the image to base64
            buffered = BytesIO()
            image.save(buffered, format="PNG")
            encoded_img = base64.b64encode(buffered.getvalue()).decode('utf-8')

            #element_image_filename = output_dir / f"{doc_filename}-table-{table_counter}.png"
            #with element_image_filename.open("wb") as fp:
            #    image.save(fp, "PNG")

            qimage = ImageQt(image)
            pixmap = QPixmap.fromImage(qimage)
            
            table_label = ClickableLabel()
            table_label.setObjectName(f"table_{table_counter}")
            table_label.setPixmap(pixmap)  # Scale for left panel
            table_label.setAlignment(Qt.AlignCenter)
            
            # Store full-size pixmap in the table_label
            table_label.full_pixmap = pixmap
            
            # Connect click handler
            table_label.clicked.connect(lambda: self.show_image_preview(table_label, encoded_img))

            self.left_content_layout.addWidget(table_label)
            
        except Exception as e:
            raise Exception(f"Table processing failed: {str(e)}") from e
        
    def on_table_click(self, label):
        try:
            dialog = QDialog(self)
            dialog.setWindowTitle("Table Image")
            dialog.setMinimumSize(800, 600)
            
            layout = QVBoxLayout()
            scroll = QScrollArea()
            content = QLabel()
            
            # Get the stored pixmap
            content.setPixmap(label.pixmap_ref)
            content.setAlignment(Qt.AlignCenter)
            
            scroll.setWidget(content)
            layout.addWidget(scroll)
            dialog.setLayout(layout)
            dialog.exec()
            
        except Exception as e:
            QMessageBox.warning(self, "Error", f"Failed to show image:\n{str(e)}")

    def show_table_details(self, table_element):
        try:
            # Example safe access to table data
            print("Table structure:", table_element.structure)
            print("Table cells:", table_element.cells)
            
            # Or show data in a dialog
            dialog = QDialog(self)
            layout = QVBoxLayout()
            table_view = QTableWidget()
            
            # Populate table widget
            if hasattr(table_element, 'cells'):
                table_view.setRowCount(len(table_element.cells))
                table_view.setColumnCount(len(table_element.cells[0]))
                for row_idx, row in enumerate(table_element.cells):
                    for col_idx, cell in enumerate(row):
                        table_view.setItem(row_idx, col_idx, QTableWidgetItem(str(cell)))
            
            layout.addWidget(table_view)
            dialog.setLayout(layout)
            dialog.exec()
            
        except Exception as e:
            QMessageBox.warning(self, "Table Error", 
                            f"Couldn't show table details:\n{str(e)}")
    def show_image_preview(self, label, encoded_img):
        """Show the full-size table in the right panel"""
        try:
            # Scale pixmap to fit right panel width while maintaining aspect ratio
            scaled_pix = label.full_pixmap.scaledToWidth(
                self.right_panel.width() - 20,  # 20px padding
                Qt.SmoothTransformation
            )
            
            #self.preview_label.setPixmap(scaled_pix)
            #self.preview_label.setAlignment(Qt.AlignCenter)
            preview = ClickableLabel()
            preview.setProperty("element_id", self.element_counter)
            preview.setPixmap(scaled_pix)
            preview.setAlignment(Qt.AlignCenter)
            #self.right_layout.addWidget(preview)
            self.previews_layout.addWidget(preview)
            self.question_data['data'].append({
                "id": self.element_counter,
                "value": encoded_img,
                "type": 'image',
            })
            self.element_counter += 1
            #preview.clicked.connect(lambda: self.clear_panel_element(preview))
        except Exception as e:
            QMessageBox.warning(self, "Preview Error", 
                              f"Failed to show table preview:\n{str(e)}")
    def show_text_preview(self, text):
        """Append a new text preview to the right panel."""
        try:
            preview = ClickableLabel()
            preview.setProperty("element_id", self.element_counter)
            preview.setText(text)
            preview.setWordWrap(True)
            #self.right_layout.addWidget(preview)
            preview.setStyleSheet("border: 1px solid gray; padding: 5px; margin-bottom: 5px;")
            self.previews_layout.addWidget(preview)
            preview.clicked.connect(lambda: self.set_text_to_points(preview))
            self.question_data['data'].append({
                "id": self.element_counter,
                "value": text,
                "type": 'question',
            })
            self.element_counter += 1
            print(self.question_data)
        except Exception as e:
            QMessageBox.warning(self, "Preview Error", f"Failed to show text preview:\n{str(e)}")

    def process_picture(self, element, document, output_dir, doc_filename):
        try:
            picture_counter = len([w for w in self.findChildren(QLabel) if "picture" in w.objectName()]) + 1
            image = element.get_image(document)

            # Encode the image to base64
            buffered = BytesIO()
            image.save(buffered, format="PNG")
            encoded_img = base64.b64encode(buffered.getvalue()).decode('utf-8')

            #lement_image_filename = output_dir / f"{doc_filename}-picture-{picture_counter}.png"
            #with element_image_filename.open("wb") as fp:
            #    image.save(fp, "PNG")
            
            qimage = ImageQt(image)
            pixmap = QPixmap.fromImage(qimage)
            
            #label = QLabel()
            picture_label = ClickableLabel()
            picture_label.setObjectName(f"picture_{picture_counter}")
            picture_label.setPixmap(pixmap)
            picture_label.setAlignment(Qt.AlignCenter)
            picture_label.full_pixmap = pixmap
            
            picture_label.clicked.connect(lambda: self.show_image_preview(picture_label, encoded_img))

            self.left_content_layout.addWidget(picture_label)

        except Exception as e:
            raise Exception(f"Picture processing failed: {str(e)}") from e

    def process_text(self, element):
        try:
            text_label = ClickableLabel()
            text_label.setObjectName("text_preview")
            
            # Get text content with fallbacks
            text = getattr(element, 'text')
            if text is None or text == '':    
                # to do: really make sure we are handling a chemical formula (there maby other scenarios where we fall here)
                text_content = getattr(element, 'orig', getattr(element, 'content', str(element)))
                empty_text = True
                text_label.setStyleSheet("border: 2px solid red; padding: 5px; margin-bottom: 5px;")
                    #getattr(element, 'orig', getattr(element, 'content', str(element))) + 
                    #'\nNOTE: ESSA FORMULA PROVAVELMENTE NÃO ESTÁ IGUAL A FORMULA DO PDF.\nGERAR UMA IMAGEM DA FORMULA E ANEXAR NO PAINEL')
            else:
                text_content = getattr(element, 'text', getattr(element, 'content', str(element)))
                text_label.setStyleSheet("border: 1px solid gray; padding: 5px; margin-bottom: 5px;")

            text_label.setText(text_content)
            text_label.setWordWrap(True)

            text_label.clicked.connect(lambda: self.show_text_preview(text_content))
            self.left_content_layout.addWidget(text_label)
        
        except Exception as e:
            error_msg = f"Text processing failed: {str(e)}"
            print(error_msg)
            QMessageBox.warning(self, "Text Error", error_msg)

    def set_text_to_points(self, element):
        element.setStyleSheet("border: 2px solid green; padding: 5px; margin-bottom: 5px;")
        elem_id = element.property("element_id")
        print("elem_id: ", elem_id)
        for i, text in enumerate(self.question_data['data']):
            print("text['id]: ", text['id'])
            if text['id'] == elem_id:
                print("ACHOU IGUAL ID")
                self.question_data['data'][i]['type'] = 'point'
                print("question_data after clicked: ", self.question_data)

    #---unused--- to do: maby remove it
    def on_element_click(self, element):
        try:
            print(f"Clicked on element: {element}")
            # Add your interaction logic here
        except Exception as e:
            QMessageBox.warning(self, "Interaction Error", f"Failed to handle click:\n{e}")

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    app = QApplication(sys.argv)
    window = PDFtoJSONApp()
    window.show()
    sys.exit(app.exec())