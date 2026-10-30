// Meninges B-scan annotation macros
// Accepts any PNG. White edge strips are cropped automatically, then the
// base calibration is converted to the current content dimensions.

var meningesCropLeft = 0;
var meningesCropTop = 0;
var meningesCropRight = 0;
var meningesCropBottom = 0;
var meningesContentWidth = 0;
var meningesContentHeight = 0;
var meningesCalX = 0;
var meningesCalY = 0;
var meningesResolvedImagePath = "";
var meningesResolvedImageId = "";

macro "Meninges - 1 Setup current image" {
    meningesRequireSourceImage();
    if (roiManager("count")>0)
        exit("ROI Manager is not empty. Export the current image first, then use More > Reset before setting up another image.");
    roiManager("Show All");
    showStatus("Meninges calibration applied to the " + meningesContentWidth + "x" + meningesContentHeight + " content area");
}

macro "Meninges - 2 Add thickness line" {
    meningesRequireSourceImage();
    if (selectionType()!=5)
        exit("Draw one Straight Line ROI first.");
    meningesAssertManagerBelongsToCurrentImage();

    nextNumber = meningesNextObjectNumber("T");
    if (nextNumber>999)
        exit("This image already uses all measurement IDs from T001 to T999.");
    numberText = meningesPad3(nextNumber);
    objectName = "T" + numberText;
    internalPrefix = meningesInternalPrefix();
    internalName = internalPrefix + objectName;
    Roi.setStrokeColor("#ffff00");
    Roi.setStrokeWidth(1);
    roiManager("Add");
    roiManager("Select", roiManager("count") - 1);
    roiManager("Rename", internalName);

    getLine(x1, y1, x2, y2, lineWidth);
    dxUm = (x2 - x1) * meningesSourceXUmPerPx();
    dyUm = (y2 - y1) * meningesSourceYUmPerPx();
    distanceUm = sqrt(dxUm * dxUm + dyUm * dyUm);
    roiManager("Show All");
    meningesSaveRecoverySnapshot();
    showStatus(objectName + " = " + d2s(distanceUm, 2) + " um; recovery snapshot saved");
}

macro "Meninges - 3 Set or replace reference line" {
    meningesRequireSourceImage();
    if (selectionType()!=5)
        exit("Draw one Straight Line ROI for the reference line first.");
    meningesAssertManagerBelongsToCurrentImage();
    getLine(x1, y1, x2, y2, lineWidth);
    if (x1==x2 && y1==y2)
        exit("The reference line must have non-zero length.");
    existingReferenceIndex = meningesFindObjectIndex("L001");
    if (existingReferenceIndex>=0) {
        if (meningesCountGroups()>0)
            exit("Delete all refractive-index groups before replacing L001.");
        roiManager("Select", existingReferenceIndex);
        roiManager("Delete");
        makeLine(x1, y1, x2, y2);
    }
    internalPrefix = meningesInternalPrefix();
    internalName = internalPrefix + "L001";
    Roi.setStrokeColor("#00ff00");
    Roi.setStrokeWidth(1);
    roiManager("Add");
    roiManager("Select", roiManager("count") - 1);
    roiManager("Rename", internalName);
    roiManager("Show All");
    meningesSaveRecoverySnapshot();
    showStatus("L001 reference line set; recovery snapshot saved");
}

macro "Meninges - 4 Add shared-foot refractive-index group" {
    meningesRequireSourceImage();
    if (selectionType()!=10)
        exit("Select the Multi-point tool, click exactly two measurement points, then run this command.");
    meningesAssertManagerBelongsToCurrentImage();

    getSelectionCoordinates(pointXs, pointYs);
    if (lengthOf(pointXs)!=2)
        exit("Exactly two points are required. The first point is fixed; the second point may snap by up to 3 source pixels.");
    point1X = pointXs[0];
    point1Y = pointYs[0];
    rawPoint2X = pointXs[1];
    rawPoint2Y = pointYs[1];
    referenceIndex = meningesFindObjectIndex("L001");
    if (referenceIndex<0)
        exit("Add the L001 reference line before creating a refractive-index group.");

    roiManager("Select", referenceIndex);
    if (selectionType()!=5)
        exit("L001 is not a Straight Line ROI.");
    getLine(lineX1, lineY1, lineX2, lineY2, lineWidth);

    // All projection and perpendicular calculations use calibrated physical
    // coordinates because x and y have different source-pixel sizes.
    scaleX = meningesSourceXUmPerPx();
    scaleY = meningesSourceYUmPerPx();
    lineDxUm = (lineX2 - lineX1) * scaleX;
    lineDyUm = (lineY2 - lineY1) * scaleY;
    denominator = lineDxUm * lineDxUm + lineDyUm * lineDyUm;
    if (denominator==0)
        exit("L001 has zero length.");

    pointDxUm = (point1X - lineX1) * scaleX;
    pointDyUm = (point1Y - lineY1) * scaleY;
    projection = (pointDxUm * lineDxUm + pointDyUm * lineDyUm) / denominator;
    footX = lineX1 + projection * (lineX2 - lineX1);
    footY = lineY1 + projection * (lineY2 - lineY1);

    lineLengthUm = sqrt(denominator);
    normalX = -lineDyUm / lineLengthUm;
    normalY = lineDxUm / lineLengthUm;
    rawPoint2DxUm = (rawPoint2X - footX) * scaleX;
    rawPoint2DyUm = (rawPoint2Y - footY) * scaleY;
    normalDistanceUm = rawPoint2DxUm * normalX + rawPoint2DyUm * normalY;
    point2X = footX + normalDistanceUm * normalX / scaleX;
    point2Y = footY + normalDistanceUm * normalY / scaleY;
    snapDxPx = point2X - rawPoint2X;
    snapDyPx = point2Y - rawPoint2Y;
    snapDistancePx = sqrt(snapDxPx * snapDxPx + snapDyPx * snapDyPx);
    if (snapDistancePx>meningesMaxSnapPx())
        exit("The second point would move " + d2s(snapDistancePx, 2)
            + " source pixels. The maximum is " + d2s(meningesMaxSnapPx(), 0)
            + ". Click closer to the common perpendicular and try again.");

    getDimensions(imageWidth, imageHeight, channels, slices, frames);
    if (footX<0 || footX>imageWidth - 1 || footY<0 || footY>imageHeight - 1)
        exit("The perpendicular foot is outside the source image. Extend or reposition L001, or choose another point.");
    if (point2X<0 || point2X>imageWidth - 1 || point2Y<0 || point2Y>imageHeight - 1)
        exit("The snapped second point is outside the source image.");

    distance1Um = meningesPhysicalDistance(point1X, point1Y, footX, footY);
    distance2Um = meningesPhysicalDistance(point2X, point2Y, footX, footY);
    if (distance1Um<0.000001 || distance2Um<0.000001)
        exit("Both selected points must have a non-zero distance from L001.");
    smallerDistanceUm = minOf(distance1Um, distance2Um);
    largerDistanceUm = maxOf(distance1Um, distance2Um);
    refractiveIndex = 1 + smallerDistanceUm / largerDistanceUm;

    groupNumber = meningesNextGroupNumber();
    if (groupNumber>999)
        exit("This image already uses all refractive-index group IDs from G001 to G999.");
    groupNumberText = meningesPad3(groupNumber);
    groupId = "G" + groupNumberText;
    internalPrefix = meningesInternalPrefix();

    firstNumber = meningesNextObjectNumber("T");
    if (firstNumber>999)
        exit("This image already uses all measurement IDs from T001 to T999.");
    firstNumberText = meningesPad3(firstNumber);
    firstObjectName = "T" + firstNumberText;
    firstInternalName = internalPrefix + firstObjectName + "__" + groupId + "A";
    makeLine(point1X, point1Y, footX, footY);
    Roi.setStrokeColor("#ffff00");
    Roi.setStrokeWidth(1);
    roiManager("Add");
    roiManager("Select", roiManager("count") - 1);
    roiManager("Rename", firstInternalName);

    secondNumber = meningesNextObjectNumber("T");
    if (secondNumber>999)
        exit("This image already uses all measurement IDs from T001 to T999.");
    secondNumberText = meningesPad3(secondNumber);
    secondObjectName = "T" + secondNumberText;
    secondInternalName = internalPrefix + secondObjectName + "__" + groupId + "B";
    makeLine(point2X, point2Y, footX, footY);
    Roi.setStrokeColor("#ffff00");
    Roi.setStrokeWidth(1);
    roiManager("Add");
    roiManager("Select", roiManager("count") - 1);
    roiManager("Rename", secondInternalName);

    roiManager("Show All");
    meningesSaveRecoverySnapshot();
    showMessage(
        "Shared-foot refractive-index group",
        groupId + "\n"
        + firstObjectName + " = " + d2s(distance1Um, 2) + " um\n"
        + secondObjectName + " = " + d2s(distance2Um, 2) + " um\n"
        + "Second-point snap = " + d2s(snapDistancePx, 2) + " px\n"
        + "n = 1 + " + d2s(smallerDistanceUm, 2) + " / "
        + d2s(largerDistanceUm, 2) + " = " + d2s(refractiveIndex, 3)
        + "\n\nRecovery snapshot saved."
    );
}

macro "Meninges - 5 Add inset ROI" {
    meningesRequireSourceImage();
    if (selectionType()!=0)
        exit("Draw one Rectangle ROI first.");
    meningesAssertManagerBelongsToCurrentImage();
    if (meningesCountKind("R")>=1)
        exit("Only one inset ROI is allowed. Delete or edit R001 in ROI Manager.");

    getSelectionBounds(x, y, roiWidth, roiHeight);
    getDimensions(imageWidth, imageHeight, channels, slices, frames);
    side = maxOf(roiWidth, roiHeight);
    if (side > minOf(imageWidth, imageHeight))
        exit("The selected ROI is too large to become a square crop.");

    squareX = x + roiWidth / 2 - side / 2;
    squareY = y + roiHeight / 2 - side / 2;
    squareX = maxOf(0, minOf(squareX, imageWidth - side));
    squareY = maxOf(0, minOf(squareY, imageHeight - side));

    makeRectangle(squareX, squareY, side, side);

    Roi.setStrokeColor("#ffffff");
    Roi.setStrokeWidth(1);
    roiManager("Add");
    roiManager("Select", roiManager("count") - 1);
    internalPrefix = meningesInternalPrefix();
    internalName = internalPrefix + "R001";
    roiManager("Rename", internalName);
    roiManager("Show All");
    meningesSaveRecoverySnapshot();
    showStatus("Optional white R001 inset frame added; recovery snapshot saved");
}

macro "Meninges - 6 Delete selected refractive-index group" {
    meningesRequireSourceImage();
    meningesAssertManagerBelongsToCurrentImage();
    selectedIndex = roiManager("index");
    if (selectedIndex<0)
        exit("Select either measurement line from the refractive-index group in ROI Manager.");
    selectedName = RoiManager.getName(selectedIndex);
    cleanName = meningesCleanObjectName(selectedName);
    groupId = meningesGroupIdFromName(cleanName);
    if (groupId=="")
        exit("The selected ROI is not part of a refractive-index group.");

    for (i=roiManager("count") - 1; i>=0; i--) {
        candidateName = RoiManager.getName(i);
        candidate = meningesCleanObjectName(candidateName);
        candidateGroup = meningesGroupIdFromName(candidate);
        if (candidateGroup==groupId) {
            roiManager("Select", i);
            roiManager("Delete");
        }
    }
    roiManager("Show All");
    meningesSaveRecoverySnapshot();
    showStatus(groupId + " deleted as one group; recovery snapshot saved");
}

macro "Meninges - 7 Validate current objects" {
    meningesRequireSourceImage();
    meningesAssertManagerBelongsToCurrentImage();
    meningesValidateCurrentObjects();
    showMessage(
        "Meninges validation",
        "Validation passed.\n\nR001 is optional. Without R001 only the global view will be generated."
    );
}

macro "Meninges - 8 Export current objects" {
    meningesRequireSourceImage();
    meningesAssertManagerBelongsToCurrentImage();
    count = roiManager("count");
    if (count==0)
        exit("ROI Manager is empty.");

    meningesValidateCurrentObjects();
    id = meningesCurrentImageId();
    measurementCount = 0;
    roiCount = 0;
    referenceCount = 0;

    projectRoot = meningesProjectRoot();
    if (!File.isDirectory(projectRoot))
        projectRoot = getDirectory("Choose the project root");
    if (projectRoot=="")
        exit("Export cancelled.");
    exportDir = projectRoot + "fiji/exports/";
    roiDir = projectRoot + "fiji/rois/";
    measurementDir = projectRoot + "fiji/measurements/";
    File.makeDirectory(exportDir);
    File.makeDirectory(roiDir);
    File.makeDirectory(measurementDir);

    objectPath = exportDir + id + ".objects.csv";
    measurementPath = measurementDir + id + ".csv";
    roiPath = roiDir + id + ".zip";

    File.saveString(
        "image_id,object_id,type,x1_px,y1_px,x2_px,y2_px,x_px,y_px,width_px,height_px,preview_value_um,measurement_kind,group_id,group_member,refractive_index\n",
        objectPath
    );
    File.saveString(
        "image_id,measurement_id,x1_px,y1_px,x2_px,y2_px,dx_px,dy_px,distance_um,unit,measurement_kind,group_id,group_member,refractive_index\n",
        measurementPath
    );

    measurementCount = 0;
    roiCount = 0;
    referenceCount = 0;
    for (i=0; i<count; i++) {
        roiManager("Select", i);
        fullName = RoiManager.getName(i);
        cleanName = meningesCleanObjectName(fullName);
        objectName = meningesBaseObjectName(cleanName);
        groupId = meningesGroupIdFromName(cleanName);
        groupMember = meningesGroupMemberFromName(cleanName);
        objectType = selectionType();
        if (objectType==5) {
            getLine(x1, y1, x2, y2, lineWidth);
            if (objectName=="L001") {
                File.append(
                    id + "," + objectName + ",reference_line,"
                    + d2s(x1, 6) + "," + d2s(y1, 6) + ","
                    + d2s(x2, 6) + "," + d2s(y2, 6)
                    + ",,,,,,,,,,\n",
                    objectPath
                );
                referenceCount++;
            } else {
                dx = x2 - x1;
                dy = y2 - y1;
                dxUm = dx * meningesSourceXUmPerPx();
                dyUm = dy * meningesSourceYUmPerPx();
                distanceUm = sqrt(dxUm * dxUm + dyUm * dyUm);
                measurementKind = "thickness";
                refractiveIndexText = "";
                if (groupId!="") {
                    measurementKind = "shared_foot_distance";
                    refractiveIndexText = d2s(
                        meningesGroupRefractiveIndex(groupId),
                        9
                    );
                }
                File.append(
                    id + "," + objectName + ",measurement,"
                    + d2s(x1, 6) + "," + d2s(y1, 6) + ","
                    + d2s(x2, 6) + "," + d2s(y2, 6)
                    + ",,,,,"
                    + d2s(distanceUm, 9) + ","
                    + measurementKind + "," + groupId + ","
                    + groupMember + "," + refractiveIndexText + "\n",
                    objectPath
                );
                File.append(
                    id + "," + objectName + ","
                    + d2s(x1, 6) + "," + d2s(y1, 6) + ","
                    + d2s(x2, 6) + "," + d2s(y2, 6) + ","
                    + d2s(dx, 6) + "," + d2s(dy, 6) + ","
                    + d2s(distanceUm, 9) + ",um,"
                    + measurementKind + "," + groupId + ","
                    + groupMember + "," + refractiveIndexText + "\n",
                    measurementPath
                );
                measurementCount++;
            }
        } else if (objectType==0) {
            getSelectionBounds(x, y, roiWidth, roiHeight);
            File.append(
                id + "," + objectName + ",roi,,,,,"
                + d2s(x, 6) + "," + d2s(y, 6) + ","
                + d2s(roiWidth, 6) + "," + d2s(roiHeight, 6)
                + ",,,,,\n",
                objectPath
            );
            roiCount++;
        }
    }

    roiManager("Deselect");
    roiManager("Save", roiPath);
    outputMode = meningesOutputMode(roiCount);
    showMessage(
        "Meninges export",
        "Image: " + id + "\nMeasurements: " + measurementCount
        + "\nReference lines: " + referenceCount
        + "\nOptional inset ROIs: " + roiCount
        + "\nOutput mode: " + outputMode + "\n\n" + objectPath
        + "\n\nBefore opening the next image, use ROI Manager > More > Reset."
    );
}

macro "Meninges - 9 Save recovery snapshot" {
    meningesRequireSourceImage();
    meningesAssertManagerBelongsToCurrentImage();
    count = roiManager("count");
    if (count==0)
        exit("ROI Manager is empty.");
    meningesSaveRecoverySnapshot();
    recoveryPath = meningesRecoveryPath();
    showMessage("Recovery snapshot saved", recoveryPath);
}

macro "Meninges - 10 Load recovery snapshot" {
    meningesRequireSourceImage();
    if (roiManager("count")>0)
        exit("ROI Manager must be empty before loading a recovery snapshot.");
    recoveryPath = meningesRecoveryPath();
    if (!File.exists(recoveryPath))
        exit("No recovery snapshot exists for this image:\n" + recoveryPath);
    roiManager("Open", recoveryPath);
    meningesRebaseLoadedManager();
    meningesAssertManagerBelongsToCurrentImage();
    roiManager("Show All");
    showStatus("Recovery snapshot loaded: " + recoveryPath);
}

function meningesRequireSourceImage() {
    if (nImages==0)
        exit("Open one PNG first.");
    meningesCurrentImageId();
    getDimensions(imageWidth, imageHeight, channels, slices, frames);
    meningesReadContentBounds();
    meningesCropContentArea();
    meningesApplyCalibration();
}

function meningesReadContentBounds() {
    meningesCropLeft = 0;
    meningesCropTop = 0;
    meningesCropRight = 0;
    meningesCropBottom = 0;
    projectRoot = meningesProjectRoot();
    boundsPath = projectRoot + "fiji/image_bounds.csv";
    if (File.exists(boundsPath)) {
        boundsText = File.openAsString(boundsPath);
        lines = split(boundsText, "\n");
        for (i=0; i<lengthOf(lines); i++) {
            line = trim(lines[i]);
            if (lengthOf(line)==0 || startsWith(line, "width"))
                continue;
            parts = split(line, ",");
            if (lengthOf(parts)!=6)
                continue;
            w = parseInt(parts[0]);
            h = parseInt(parts[1]);
            if (w==imageWidth && h==imageHeight) {
                meningesCropLeft = parseInt(parts[2]);
                meningesCropTop = parseInt(parts[3]);
                meningesCropRight = parseInt(parts[4]);
                meningesCropBottom = parseInt(parts[5]);
                break;
            }
        }
    }
    meningesContentWidth = imageWidth - meningesCropLeft - meningesCropRight;
    meningesContentHeight = imageHeight - meningesCropTop - meningesCropBottom;
    if (meningesContentWidth<=0 || meningesContentHeight<=0)
        exit("Invalid content bounds from fiji/image_bounds.csv.");
}

function meningesCropContentArea() {
    if (meningesCropLeft+meningesCropTop+meningesCropRight+meningesCropBottom==0)
        return 1;
    if (roiManager("count")>0)
        exit("This image still needs its content-area crop, but ROI Manager is not empty. Reset ROI Manager, then run Setup before adding objects.");
    makeRectangle(
        meningesCropLeft,
        meningesCropTop,
        meningesContentWidth,
        meningesContentHeight
    );
    run("Crop");
    getDimensions(imageWidth, imageHeight, channels, slices, frames);
    meningesCropLeft = 0;
    meningesCropTop = 0;
    meningesCropRight = 0;
    meningesCropBottom = 0;
    meningesContentWidth = imageWidth;
    meningesContentHeight = imageHeight;
    return 1;
}

function meningesCurrentImageId() {
    imagePath = meningesCurrentImagePath();
    projectRoot = replace(meningesProjectRoot(), "\\", "/");
    rawRoot = projectRoot + "raw/";
    if (imagePath!=meningesResolvedImagePath
        && startsWith(toLowerCase(imagePath), toLowerCase(rawRoot))) {
        meningesResolvedImagePath = imagePath;
        meningesResolvedImageId = meningesImageIdFromPath(imagePath);
    }
    if (meningesResolvedImageId=="")
        exit("Could not resolve the current raw image ID.");
    return meningesResolvedImageId;
}

function meningesCurrentImagePath() {
    directory = replace(getInfo("image.directory"), "\\", "/");
    fileName = replace(getInfo("image.filename"), "\\", "/");
    return directory + fileName;
}

function meningesImageIdFromPath(imagePath) {
    projectRoot = replace(meningesProjectRoot(), "\\", "/");
    rawRoot = projectRoot + "raw/";
    normalizedPath = replace(imagePath, "\\", "/");
    if (!startsWith(toLowerCase(normalizedPath), toLowerCase(rawRoot)))
        exit("Open a PNG below the project's raw/ directory.");
    relativePath = substring(normalizedPath, lengthOf(rawRoot));
    dot = lastIndexOf(relativePath, ".");
    if (dot<=0)
        exit("The current image does not have a valid PNG filename.");
    relativePath = substring(relativePath, 0, dot);
    components = split(relativePath, "/");
    if (lengthOf(components)==0)
        exit("Could not resolve the current raw image ID.");
    imageId = "";
    for (i=0; i<lengthOf(components); i++) {
        if (i>0)
            imageId = imageId + "__";
        imageId = imageId + meningesEncodeIdComponent(components[i]);
    }
    return imageId;
}

function meningesEncodeIdComponent(component) {
    component = replace(component, "%", "%25");
    component = replace(component, "__", "_%5F");
    return component;
}

function meningesInternalPrefix() {
    imageId = meningesCurrentImageId();
    prefix = imageId + "__";
    return prefix;
}

function meningesCleanObjectName(fullName) {
    prefix = meningesInternalPrefix();
    if (!startsWith(fullName, prefix))
        return "";
    return substring(fullName, lengthOf(prefix));
}

function meningesBaseObjectName(cleanName) {
    separator = indexOf(cleanName, "__");
    if (separator<0)
        return cleanName;
    return substring(cleanName, 0, separator);
}

function meningesGroupIdFromName(cleanName) {
    separator = indexOf(cleanName, "__");
    if (separator<0)
        return "";
    token = substring(cleanName, separator + 2);
    if (lengthOf(token)!=5 || !startsWith(token, "G"))
        return "";
    groupId = substring(token, 0, 4);
    groupNumber = parseInt(substring(groupId, 1));
    groupNumberText = meningesPad3(groupNumber);
    expectedGroupId = "G" + groupNumberText;
    if (groupNumber<1 || groupId!=expectedGroupId)
        return "";
    member = substring(token, 4, 5);
    if (member!="A" && member!="B")
        return "";
    return groupId;
}

function meningesGroupMemberFromName(cleanName) {
    groupId = meningesGroupIdFromName(cleanName);
    if (groupId=="")
        return "";
    return substring(cleanName, lengthOf(cleanName) - 1);
}

function meningesAssertManagerBelongsToCurrentImage() {
    count = roiManager("count");
    prefix = meningesInternalPrefix();
    for (i=0; i<count; i++) {
        name = RoiManager.getName(i);
        if (!startsWith(name, prefix))
            exit("ROI Manager contains an object from another image or an older macro version: "
                + name + "\nExport/reset that ROI set before continuing.");
    }
}

function meningesRebaseLoadedManager() {
    prefix = meningesInternalPrefix();
    for (i=0; i<roiManager("count"); i++) {
        name = RoiManager.getName(i);
        separator = indexOf(name, "__");
        if (separator<0)
            exit("Recovery snapshot contains an invalid ROI name: " + name);
        suffix = substring(name, separator + 2);
        rebasedName = prefix + suffix;
        if (name!=rebasedName) {
            roiManager("Select", i);
            roiManager("Rename", rebasedName);
        }
    }
}

function meningesCountKind(kind) {
    count = roiManager("count");
    prefix = meningesInternalPrefix();
    kindPrefix = prefix + kind;
    kindCount = 0;
    for (i=0; i<count; i++) {
        name = RoiManager.getName(i);
        if (startsWith(name, kindPrefix))
            kindCount++;
    }
    return kindCount;
}

function meningesObjectNameExists(objectName) {
    count = roiManager("count");
    for (i=0; i<count; i++) {
        name = RoiManager.getName(i);
        cleanName = meningesCleanObjectName(name);
        baseName = meningesBaseObjectName(cleanName);
        if (baseName==objectName)
            return 1;
    }
    return 0;
}

function meningesFindObjectIndex(objectName) {
    count = roiManager("count");
    prefix = meningesInternalPrefix();
    fullName = prefix + objectName;
    for (i=0; i<count; i++) {
        if (RoiManager.getName(i)==fullName)
            return i;
    }
    return -1;
}

function meningesNextObjectNumber(kind) {
    number = 1;
    numberText = meningesPad3(number);
    candidateName = kind + numberText;
    while (meningesObjectNameExists(candidateName)) {
        number++;
        numberText = meningesPad3(number);
        candidateName = kind + numberText;
    }
    return number;
}

function meningesNextGroupNumber() {
    number = 1;
    numberText = meningesPad3(number);
    candidate = "G" + numberText;
    found = 1;
    while (found) {
        found = 0;
        for (i=0; i<roiManager("count"); i++) {
            name = RoiManager.getName(i);
            cleanName = meningesCleanObjectName(name);
            cleanGroup = meningesGroupIdFromName(cleanName);
            if (cleanGroup==candidate)
                found = 1;
        }
        if (found) {
            number++;
            numberText = meningesPad3(number);
            candidate = "G" + numberText;
        }
    }
    return number;
}

function meningesCountGroups() {
    groupCount = 0;
    for (i=0; i<roiManager("count"); i++) {
        name = RoiManager.getName(i);
        cleanName = meningesCleanObjectName(name);
        cleanGroup = meningesGroupIdFromName(cleanName);
        cleanMember = meningesGroupMemberFromName(cleanName);
        if (cleanGroup!="" && cleanMember=="A")
            groupCount++;
    }
    return groupCount;
}

function meningesIsValidMeasurementName(objectName) {
    if (lengthOf(objectName)!=4 || !startsWith(objectName, "T"))
        return 0;
    number = parseInt(substring(objectName, 1));
    if (number<1)
        return 0;
    numberText = meningesPad3(number);
    expectedName = "T" + numberText;
    return objectName==expectedName;
}

function meningesFindGroupMemberIndex(groupId, member) {
    foundIndex = -1;
    for (i=0; i<roiManager("count"); i++) {
        name = RoiManager.getName(i);
        cleanName = meningesCleanObjectName(name);
        cleanGroup = meningesGroupIdFromName(cleanName);
        cleanMember = meningesGroupMemberFromName(cleanName);
        if (cleanGroup==groupId && cleanMember==member) {
            if (foundIndex>=0)
                exit(groupId + " contains duplicate member " + member + ".");
            foundIndex = i;
        }
    }
    return foundIndex;
}

function meningesValidateSharedFootGroup(groupId) {
    firstIndex = meningesFindGroupMemberIndex(groupId, "A");
    secondIndex = meningesFindGroupMemberIndex(groupId, "B");
    if (firstIndex<0 || secondIndex<0)
        exit(groupId + " must contain exactly members A and B.");
    referenceIndex = meningesFindObjectIndex("L001");
    if (referenceIndex<0)
        exit(groupId + " requires L001.");

    roiManager("Select", firstIndex);
    if (selectionType()!=5)
        exit(groupId + " member A is not a Straight Line ROI.");
    getLine(firstX, firstY, firstFootX, firstFootY, lineWidth);

    roiManager("Select", secondIndex);
    if (selectionType()!=5)
        exit(groupId + " member B is not a Straight Line ROI.");
    getLine(secondX, secondY, secondFootX, secondFootY, lineWidth);

    footGapPx = sqrt(
        (firstFootX - secondFootX) * (firstFootX - secondFootX)
        + (firstFootY - secondFootY) * (firstFootY - secondFootY)
    );
    if (footGapPx>0.00001)
        exit(groupId + " perpendicular feet do not coincide.");

    roiManager("Select", referenceIndex);
    getLine(lineX1, lineY1, lineX2, lineY2, lineWidth);
    scaleX = meningesSourceXUmPerPx();
    scaleY = meningesSourceYUmPerPx();
    lineDxUm = (lineX2 - lineX1) * scaleX;
    lineDyUm = (lineY2 - lineY1) * scaleY;
    denominator = lineDxUm * lineDxUm + lineDyUm * lineDyUm;
    if (denominator==0)
        exit("L001 has zero length.");

    footDxUm = (firstFootX - lineX1) * scaleX;
    footDyUm = (firstFootY - lineY1) * scaleY;
    projection = (footDxUm * lineDxUm + footDyUm * lineDyUm) / denominator;
    projectedFootX = lineX1 + projection * (lineX2 - lineX1);
    projectedFootY = lineY1 + projection * (lineY2 - lineY1);
    footLineErrorUm = meningesPhysicalDistance(
        firstFootX, firstFootY, projectedFootX, projectedFootY
    );
    if (footLineErrorUm>0.0001)
        exit(groupId + " common foot is no longer on L001. Recreate the group.");

    firstDxUm = (firstFootX - firstX) * scaleX;
    firstDyUm = (firstFootY - firstY) * scaleY;
    secondDxUm = (secondFootX - secondX) * scaleX;
    secondDyUm = (secondFootY - secondY) * scaleY;
    firstLengthUm = sqrt(firstDxUm * firstDxUm + firstDyUm * firstDyUm);
    secondLengthUm = sqrt(secondDxUm * secondDxUm + secondDyUm * secondDyUm);
    lineLengthUm = sqrt(denominator);
    if (firstLengthUm<0.000001 || secondLengthUm<0.000001)
        exit(groupId + " contains a zero-length distance.");
    firstDot = abs(lineDxUm * firstDxUm + lineDyUm * firstDyUm)
        / (lineLengthUm * firstLengthUm);
    secondDot = abs(lineDxUm * secondDxUm + lineDyUm * secondDyUm)
        / (lineLengthUm * secondLengthUm);
    if (firstDot>0.00001 || secondDot>0.00001)
        exit(groupId + " is no longer perpendicular to L001. Recreate the group.");
    return 1;
}

function meningesValidateCurrentObjects() {
    count = roiManager("count");
    if (count==0)
        exit("ROI Manager is empty.");
    getDimensions(imageWidth, imageHeight, channels, slices, frames);
    roiCount = 0;
    referenceCount = 0;

    for (i=0; i<count; i++) {
        fullName = RoiManager.getName(i);
        cleanName = meningesCleanObjectName(fullName);
        objectName = meningesBaseObjectName(cleanName);
        if (cleanName=="" || objectName=="")
            exit("ROI " + (i + 1) + " has an invalid or missing object ID.");
        for (j=i + 1; j<count; j++) {
            otherName = RoiManager.getName(j);
            otherCleanName = meningesCleanObjectName(otherName);
            otherBaseName = meningesBaseObjectName(otherCleanName);
            if (objectName==otherBaseName)
                exit("Duplicate object ID: " + objectName);
        }

        roiManager("Select", i);
        objectType = selectionType();
        if (objectType==5) {
            getLine(x1, y1, x2, y2, lineWidth);
            if (x1<0 || x1>imageWidth - 1 || x2<0 || x2>imageWidth - 1
                || y1<0 || y1>imageHeight - 1 || y2<0 || y2>imageHeight - 1)
                exit(objectName + " has an endpoint outside the source image.");
            if (x1==x2 && y1==y2)
                exit(objectName + " has zero length.");
            if (objectName=="L001") {
                if (cleanName!="L001")
                    exit("L001 must not contain group metadata.");
                referenceCount++;
            } else {
                if (meningesIsValidMeasurementName(objectName)==0)
                    exit("Straight-line ROI must use L001 or an ID such as T001: " + objectName);
                groupId = meningesGroupIdFromName(cleanName);
                if (indexOf(cleanName, "__")>=0 && groupId=="")
                    exit(objectName + " has invalid refractive-index group metadata.");
            }
        } else if (objectType==0) {
            if (cleanName!="R001")
                exit("The only rectangle ROI must be named R001.");
            getSelectionBounds(x, y, roiWidth, roiHeight);
            if (roiWidth<=0 || roiHeight<=0)
                exit("R001 must have positive dimensions.");
            if (abs(roiWidth - roiHeight)>0.000001)
                exit("R001 must be square in source-pixel coordinates.");
            if (x<0 || y<0 || x + roiWidth>imageWidth || y + roiHeight>imageHeight)
                exit("R001 is outside the source image.");
            roiCount++;
        } else {
            exit("Unsupported ROI type for " + objectName
                + ". Only straight lines and one optional rectangle are allowed.");
        }
    }
    if (roiCount>1)
        exit("At most one optional square inset ROI named R001 is allowed.");
    if (referenceCount>1)
        exit("At most one reference line named L001 is allowed.");

    for (i=0; i<count; i++) {
        name = RoiManager.getName(i);
        cleanName = meningesCleanObjectName(name);
        groupId = meningesGroupIdFromName(cleanName);
        if (groupId!="")
            meningesValidateSharedFootGroup(groupId);
    }
    return 1;
}

function meningesGroupRefractiveIndex(groupId) {
    firstIndex = meningesFindGroupMemberIndex(groupId, "A");
    secondIndex = meningesFindGroupMemberIndex(groupId, "B");
    if (firstIndex<0 || secondIndex<0)
        exit(groupId + " is incomplete.");
    roiManager("Select", firstIndex);
    getLine(x1, y1, x2, y2, lineWidth);
    firstDistanceUm = meningesPhysicalDistance(x1, y1, x2, y2);
    roiManager("Select", secondIndex);
    getLine(x1, y1, x2, y2, lineWidth);
    secondDistanceUm = meningesPhysicalDistance(x1, y1, x2, y2);
    return 1 + minOf(firstDistanceUm, secondDistanceUm)
        / maxOf(firstDistanceUm, secondDistanceUm);
}

function meningesPhysicalDistance(x1, y1, x2, y2) {
    dxUm = (x2 - x1) * meningesSourceXUmPerPx();
    dyUm = (y2 - y1) * meningesSourceYUmPerPx();
    return sqrt(dxUm * dxUm + dyUm * dyUm);
}

function meningesMaxSnapPx() {
    return maxOf(3, 3 * meningesContentWidth / 800);
}

function meningesOutputMode(roiCount) {
    if (roiCount==1)
        return "global + crop";
    return "global only";
}

function meningesRecoveryPath() {
    projectRoot = meningesProjectRoot();
    recoveryDir = projectRoot + "fiji/recovery/";
    File.makeDirectory(recoveryDir);
    imageId = meningesCurrentImageId();
    fileName = imageId + ".autosave.zip";
    recoveryPath = recoveryDir + fileName;
    return recoveryPath;
}

function meningesSaveRecoverySnapshot() {
    count = roiManager("count");
    if (count==0)
        return 0;
    recoveryPath = meningesRecoveryPath();
    // Deselecting forces ROI Manager to save the complete list as a ZIP set.
    roiManager("Deselect");
    roiManager("Save", recoveryPath);
    return 1;
}

function meningesPad3(value) {
    text = "" + value;
    while (lengthOf(text)<3)
        text = "0" + text;
    return text;
}

function meningesSourceXUmPerPx() {
    if (meningesCalX>0)
        return meningesCalX;
    return 6.5624864 * 800 / meningesContentWidth;
}

function meningesSourceYUmPerPx() {
    if (meningesCalY>0)
        return meningesCalY;
    return 10.34538099670395 * 180 / meningesContentHeight;
}

function meningesApplyCalibration() {
    meningesCalX = 6.5624864 * 800 / meningesContentWidth;
    meningesCalY = 10.34538099670395 * 180 / meningesContentHeight;
    setVoxelSize(
        meningesCalX,
        meningesCalY,
        1,
        "um"
    );
}

function meningesProjectRoot() {
    root = "__HANDOFF_PROJECT_ROOT__/";
    if (startsWith(root, "__HANDOFF_"))
        exit("Install the project macros with scripts/launch-fiji.ps1 first.");
    return root;
}
