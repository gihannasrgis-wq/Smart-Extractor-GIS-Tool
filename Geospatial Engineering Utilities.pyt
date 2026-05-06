import arcpy
import os
import re
import math

class Toolbox(object):
    def __init__(self):
        self.label = "Geospatial Engineering Utilities"
        self.alias = "sewer_pro"
        self.tools = [SmartSewerNetworkBuilder]

class SmartSewerNetworkBuilder(object):
    def __init__(self):
        self.label = "Smart Extractor"
        self.description = "تحويل CAD إلى GIS مع بناء شبكة هندسية متكاملة (تصحيح اتصال + أقطار + اتجاهات)"
        self.canRunInBackground = False

    def getParameterInfo(self):
        p0 = arcpy.Parameter(displayName="Select CAD File", name="cad_file", datatype="DEFile", parameterType="Required", direction="Input")
        p1 = arcpy.Parameter(displayName="Target Coordinate System", name="spatial_ref", datatype="GPSpatialReference", parameterType="Required", direction="Input")
        p2 = arcpy.Parameter(displayName="Output GDB", name="output_gdb", datatype="DEWorkspace", parameterType="Required", direction="Input")
        p3 = arcpy.Parameter(displayName="Snapping Tolerance", name="snap_dist", datatype="GPDouble", parameterType="Required", direction="Input")
        p3.value = 0.05
        return [p0, p1, p2, p3]

    def execute(self, parameters, messages):
        cad_path = parameters[0].valueAsText
        spatial_ref = parameters[1].value
        gdb_path = parameters[2].valueAsText
        snap_dist = parameters[3].value

        arcpy.env.overwriteOutput = True
        arcpy.env.outputCoordinateSystem = spatial_ref

        try:
            # 1. Feature Dataset Setup
            ds_name = "Final_Network"
            out_ds = os.path.join(gdb_path, ds_name)
            if not arcpy.Exists(out_ds):
                arcpy.management.CreateFeatureDataset(gdb_path, ds_name, spatial_ref)

            # 2. Scratch GDB for processing
            temp_gdb = os.path.join(arcpy.env.scratchFolder, "NetworkWork.gdb")
            if arcpy.Exists(temp_gdb):
                arcpy.management.Delete(temp_gdb)
            arcpy.management.CreateFileGDB(arcpy.env.scratchFolder, "NetworkWork")

            arcpy.AddMessage("Phase 1: CAD Conversion...")
            arcpy.conversion.CADToGeodatabase(cad_path, temp_gdb, "Master", "1000", spatial_ref)

            arcpy.env.workspace = temp_gdb
            datasets = arcpy.ListDatasets()

            if datasets:
                for ds in datasets:
                    fcs = arcpy.ListFeatureClasses(feature_dataset=ds)
                    for fc in fcs:
                        desc = arcpy.Describe(fc)
                        stype = desc.shapeType
                        if stype not in ["Polyline", "Point"]:
                            continue

                        # Ensure Layer field exists
                        fields = arcpy.ListFields(fc)
                        if not any(f.name == "Layer" for f in fields):
                            continue

                        # Extract unique layers
                        layers = sorted(set(row[0] for row in arcpy.da.SearchCursor(fc, ["Layer"]) if row[0]))

                        for lyr in layers:
                            # Process only Sewer-related layers if needed
                            if not lyr.upper().startswith("SWR"):
                                continue

                            # Name Cleaning and Validation
                            clean_name = re.sub(r'[^a-zA-Z0-9]', '_', lyr)
                            clean_name = re.sub(r'_+', '_', clean_name).strip('_')
                            if not clean_name: clean_name = "SWR_Layer"

                            safe_name = arcpy.ValidateTableName(clean_name + "_" + stype, out_ds)
                            final_path = os.path.join(out_ds, safe_name)

                            where = "Layer = '{}'".format(lyr.replace("'", "''"))
                            temp_view = "temp_view_" + clean_name
                            arcpy.management.MakeFeatureLayer(fc, temp_view, where)

                            if int(arcpy.management.GetCount(temp_view)[0]) > 0:
                                arcpy.management.CopyFeatures(temp_view, final_path)

                                # --- Polyline Specific Logic ---
                                if stype == "Polyline":
                                    # Direction Calculation (Azimuth)
                                    arcpy.management.AddField(final_path, "Flow_Deg", "DOUBLE")
                                    with arcpy.da.UpdateCursor(final_path, ["SHAPE@", "Flow_Deg"]) as cur:
                                        for row in cur:
                                            p1 = row[0].firstPoint
                                            p2 = row[0].lastPoint
                                            angle = math.degrees(math.atan2(p2.Y - p1.Y, p2.X - p1.X))
                                            row[1] = angle if angle >= 0 else angle + 360
                                            cur.updateRow(row)

                                    # Length Calculation
                                    arcpy.management.AddField(final_path, "Length_m", "DOUBLE")
                                    arcpy.management.CalculateGeometryAttributes(final_path, [["Length_m", "LENGTH"]], length_unit="METERS")

                                    # Topological Snapping
                                    snap_env = "{} Meters".format(snap_dist)
                                    arcpy.edit.Snap(final_path, [[final_path, "END", snap_env], 
                                                                 [final_path, "VERTEX", snap_env]])

                                # --- Attribute Cleanup ---
                                str_fields = [f.name for f in arcpy.ListFields(final_path) if f.type == "String"]
                                if str_fields:
                                    with arcpy.da.UpdateCursor(final_path, str_fields) as ucur:
                                        for row in ucur:
                                            new_row = [v.replace("%%c", "Ø") if (v and isinstance(v, str)) else v for v in row]
                                            ucur.updateRow(new_row)

                                arcpy.AddMessage("Successfully Processed: {}".format(lyr))
                            
                            if arcpy.Exists(temp_view):
                                arcpy.management.Delete(temp_view)

            arcpy.AddMessage("Smart Utilities Builder: Network Completed Successfully!")

        except Exception as e:
            arcpy.AddError("Error: {}".format(str(e)))