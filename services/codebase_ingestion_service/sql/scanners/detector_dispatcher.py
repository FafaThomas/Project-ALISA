from sql.scanners.generic_detector import GenericDetector

class SQLDetectorDispatcher:

    def __init__(self):

        self.detectors = [

            GenericDetector(),

        ]

    def detect(self, document):

        best = None

        for detector in self.detectors:

            result = detector.detect(document)

            if best is None:

                best = result

            elif result.confidence > best.confidence:

                best = result

        return best